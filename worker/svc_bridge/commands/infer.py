"""推理执行：批次目录、参数映射、批量隔离、指标报告。

真正的模型调用在 `_real_runner`（延迟导入上游 `inference.infer_tool.Svc`）。
测试注入 `runner`，因此批次/失败隔离/报告逻辑不依赖 torch 与真实权重。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..audio import clipping_ratio, peak_dbfs, protect_peak, read_wav, resample_linear, to_mono_if_needed, write_wav, trim_silence

Runner = Callable[[Dict[str, Any], Path], None]

TARGET_SR = 44100


def resolve_device(explicit: Optional[str] = None, cuda_available: Optional[bool] = None) -> str:
    """推理设备：显式优先；否则有 CUDA 用 cuda，没有就 cpu。

    上游 `Svc(device=None)` 自己也会这样选，但我们把结果写进报告，
    用户能一眼看出"为什么这么慢"。
    """
    if explicit:
        return explicit
    if cuda_available is None:
        try:
            import torch  # noqa: F401

            cuda_available = bool(__import__("torch").cuda.is_available())
        except Exception:  # noqa: BLE001
            cuda_available = False
    return "cuda" if cuda_available else "cpu"


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M", time.localtime())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def params_hash(params: Dict[str, Any]) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def to_svc_kwargs(params: Dict[str, Any], spk: str, raw_audio_path: Path) -> Dict[str, Any]:
    """UI 参数 → 上游 `Svc.slice_inference` 关键字（名字严格对齐 inference_main.py）。"""
    return {
        "raw_audio_path": str(raw_audio_path),
        "spk": spk,
        "tran": int(params.get("trans") or 0),
        "slice_db": int(params.get("sliceDb") or -40),
        "cluster_infer_ratio": float(params.get("clusterInferRatio") or 0.0),
        "auto_predict_f0": bool(params.get("autoPredictF0")),
        "noice_scale": float(params.get("noiseScale") or 0.4),
        "pad_seconds": float(params.get("padSeconds") or 0.5),
        "clip_seconds": float(params.get("clip") or 0.0),
        "lg_num": float(params.get("linearGradient") or 0.0),
        "lgr_num": float(params.get("linearGradientRetain") or 0.75),
        "f0_predictor": params.get("f0Predictor"),
        "enhancer_adaptive_key": int(params.get("enhancerAdaptiveKey") or 0),
        "cr_threshold": float(params.get("f0FilterThreshold") or 0.05),
        "k_step": int(params.get("kStep") or 100),
        "use_spk_mix": bool(params.get("useSpkMix")),
        "second_encoding": bool(params.get("secondEncoding")),
        "loudness_envelope_adjustment": float(params.get("loudnessEnvelopeAdjustment") or 1.0),
    }


def normalize_input(src: Path, dst: Path) -> None:
    """推理输入统一成 44.1k 单声道、峰值 -1dBFS；原文件不动。"""
    wav = read_wav(src)
    mono = to_mono_if_needed(wav)
    trimmed = trim_silence(mono, wav.sample_rate)
    resampled = resample_linear(trimmed, wav.sample_rate, TARGET_SR)
    write_wav(dst, protect_peak(resampled, -1.0), TARGET_SR, 1)


def prepare_batch(
    root: Path, batch_name: str, inputs: List[Path]
) -> tuple[Path, List[tuple[Path, str]]]:
    """返回 (批次目录, 预处理失败清单)。单个坏文件不能拖垮整批。"""
    batch = root / "outputs" / f"{_now_stamp()}_{batch_name}"
    (batch / "inputs").mkdir(parents=True, exist_ok=True)
    failures: List[tuple[Path, str]] = []
    for src in inputs:
        try:
            normalize_input(src, batch / "inputs" / src.name)
        except Exception as exc:  # noqa: BLE001 - 坏文件进失败清单
            failures.append((src, f"{type(exc).__name__}: {exc}"))
    return batch, failures


def _real_runner_factory(
    model_path: str,
    config_path: str,
    diffusion_model_path: Optional[str],
    diffusion_config_path: Optional[str],
    cluster_model_path: str,
    enhance: bool,
    shallow_diffusion: bool,
    only_diffusion: bool,
    feature_retrieval: bool,
    device: Optional[str] = None,
) -> Runner:
    # 延迟导入：没有 torch 时给出可读错误，而不是在 import 阶段炸掉整个 worker。
    from inference.infer_tool import Svc  # type: ignore
    import soundfile  # type: ignore

    svc = Svc(
        model_path,
        config_path,
        device,
        cluster_model_path,
        enhance,
        diffusion_model_path or "logs/44k/diffusion/model_0.pt",
        diffusion_config_path or "logs/44k/diffusion/config.yaml",
        shallow_diffusion,
        only_diffusion,
        False,
        feature_retrieval,
    )

    def runner(kwargs: Dict[str, Any], output: Path) -> None:
        audio = svc.slice_inference(**kwargs)
        soundfile.write(str(output), audio, svc.target_sample, format=output.suffix.lstrip(".") or "flac")

    return runner


def _samples_of(path: Path):
    """读样点做峰值/削波统计：wav 用自研解析，其它格式用 soundfile。"""
    if path.suffix.lower() in {".wav", ".wave"}:
        try:
            return read_wav(path).samples
        except Exception:  # noqa: BLE001
            pass
    try:
        import array

        import numpy as np
        import soundfile  # type: ignore

        data, _ = soundfile.read(str(path), dtype="float32", always_2d=False)
        data = np.clip(np.asarray(data).reshape(-1), -1.0, 1.0)
        ints = (data * 32767.0).astype("<i2")
        return array.array("h", ints.tobytes())
    except Exception:  # noqa: BLE001
        return None


def _peak_dbfs(path: Path) -> float:
    samples = _samples_of(path)
    return peak_dbfs(samples) if samples is not None else 0.0


def _clip_ratio(path: Path) -> float:
    samples = _samples_of(path)
    return clipping_ratio(samples) if samples is not None else 0.0


def _seconds_of(path: Path) -> float:
    """读音频时长：wav 用自研解析，其它格式（flac 等）用 soundfile。"""
    if path.suffix.lower() in {".wav", ".wave"}:
        try:
            return read_wav(path).seconds
        except Exception:  # noqa: BLE001
            pass
    try:
        import soundfile  # type: ignore

        info = soundfile.info(str(path))
        return float(info.frames) / float(info.samplerate or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def run_batch(
    root: Path,
    model_path: str,
    config_path: str,
    params: Dict[str, Any],
    spk: str,
    inputs: List[Path],
    batch_name: str = "batch",
    runner: Optional[Runner] = None,
    runner_factory: Optional[Callable[[], Runner]] = None,
    on_item: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    batch, prep_failures = prepare_batch(root, batch_name, inputs)
    out_dir = batch / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    if runner is None:
        if runner_factory is None:
            runner = _real_runner_factory(
                model_path,
                config_path,
                params.get("diffusionModelPath"),
                params.get("diffusionConfigPath"),
                params.get("clusterModelPath") or "",
                bool(params.get("enhance")),
                bool(params.get("shallowDiffusion")),
                bool(params.get("onlyDiffusion")),
                bool(params.get("featureRetrieval")),
                device=resolve_device(params.get("device")),
            )
        else:
            runner = runner_factory()

    fmt = str(params.get("wavFormat") or "flac")
    items: List[Dict[str, Any]] = []
    started_all = time.time()

    for src, error in prep_failures:
        items.append(
            {
                "input": str(src),
                "inputSha256": None,
                "output": None,
                "status": "failed",
                "error": error,
            }
        )

    failed_sources = {str(src) for src, _ in prep_failures}
    for src in inputs:
        if str(src) in failed_sources:
            continue
        staged = batch / "inputs" / src.name
        out_path = out_dir / f"{Path(src.name).stem}.{fmt}"
        item: Dict[str, Any] = {
            "input": str(staged),
            "inputSha256": _sha256_file(staged) if staged.is_file() else None,
            "output": str(out_path),
            "status": "queued",
        }
        try:
            kwargs = to_svc_kwargs(params, spk, staged)
            t0 = time.time()
            runner(kwargs, out_path)
            elapsed = time.time() - t0
            seconds_out = _seconds_of(out_path)
            seconds_in = _seconds_of(staged)
            item.update(
                {
                    "status": "ok",
                    "rtf": round(elapsed / seconds_in, 3) if seconds_in else None,
                    "secondsIn": round(seconds_in, 3),
                    "secondsOut": round(seconds_out, 3),
                    "peakDbfs": round(_peak_dbfs(out_path), 2),
                    "clipRatio": round(_clip_ratio(out_path), 6),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮批次
            item.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        if on_item is not None:
            try:
                on_item(len(items) + 1, len(inputs), item)
            except Exception:  # noqa: BLE001
                pass
        items.append(item)

    ok = sum(1 for i in items if i["status"] == "ok")
    failed = len(items) - ok
    status = "succeeded" if failed == 0 else ("failed" if ok == 0 else "succeeded_with_errors")

    report = {
        "schema": "svcstudio.infer/1",
        "batch": str(batch),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "modelPath": model_path,
        "configPath": config_path,
        "spk": spk,
        "device": resolve_device(params.get("device")),
        "params": params,
        "paramsHash": params_hash(params),
        "items": items,
        "summary": {
            "ok": ok,
            "failed": failed,
            "status": status,
            "elapsedSeconds": round(time.time() - started_all, 1),
        },
    }

    report_path = batch / "report.json"
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, report_path)
    return report


def retry_failed(report: Dict[str, Any], runner: Runner, fmt: Optional[str] = None) -> Dict[str, Any]:
    """只重跑失败项；成功项保持原样。"""
    items = report["items"]
    fmt = fmt or str(report["params"].get("wavFormat") or "flac")
    for item in items:
        if item["status"] == "ok":
            continue
        if not item.get("input") or not item.get("output"):
            item["error"] = "原始输入不可用（导入阶段就失败了），需要重新提供文件"
            continue
        src = Path(item["input"])
        out_path = Path(item["output"])
        try:
            runner(to_svc_kwargs(report["params"], report.get("spk", ""), src), out_path)
            wav = read_wav(out_path)
            item.update(
                {
                    "status": "ok",
                    "secondsOut": round(wav.seconds, 3),
                    "peakDbfs": round(peak_dbfs(wav.samples), 2),
                    "clipRatio": round(clipping_ratio(wav.samples), 6),
                }
            )
            item.pop("error", None)
        except Exception as exc:  # noqa: BLE001
            item["error"] = f"{type(exc).__name__}: {exc}"
    ok = sum(1 for i in items if i["status"] == "ok")
    failed = len(items) - ok
    report["summary"].update(
        {
            "ok": ok,
            "failed": failed,
            "status": "succeeded" if failed == 0 else ("failed" if ok == 0 else "succeeded_with_errors"),
        }
    )
    return report
