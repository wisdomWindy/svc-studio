"""配置校验与前置断言（D3 / D4）。

两份配置的类型与范围都按 4.1-Stable 模板校验；断言失败一律带可读原因与字段路径，
前端不需要自己拼文案。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ENCODER_DIM = {
    "vec768l12": 768,
    "vec256l9": 256,
    "hubertsoft": 256,
    "whisper-ppg": 1024,
    "whisper-ppg-large": 1280,
    "cnhubertlarge": 1024,
    "dphubert": 768,
    "wavlmbase+": 768,
}

VOCODERS = {"nsf-hifigan", "nsf-snake-hifigan"}
WHISPER_ENCODERS = {"whisper-ppg", "whisper-ppg-large"}

CONFIG_REQUIRED = [
    "train.batch_size",
    "train.epochs",
    "train.learning_rate",
    "train.keep_ckpts",
    "train.all_in_mem",
    "train.fp16_run",
    "model.vocoder_name",
    "model.ssl_dim",
    "data.sampling_rate",
]

DIFFUSION_REQUIRED = [
    "data.duration",
    "train.batch_size",
    "model.timesteps",
    "model.k_step_max",
    "vocoder.type",
]


def _get(doc: Dict[str, Any], dotted: str) -> Any:
    node: Any = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


class _Missing:
    pass


_MISSING = _Missing()


def _issue(code: str, path: str, message: str, detail: str = "") -> Dict[str, str]:
    return {"code": code, "path": path, "message": message, "detail": detail}


def load_config(path: str) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text)


def load_diffusion(path: str) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("diffusion.yaml 顶层必须是映射")
    return data


def validate_config(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    for key in CONFIG_REQUIRED:
        if _get(doc, key) is _MISSING:
            errors.append(_issue("SVC-4001", key, "缺少必需字段", "按 config 模板补齐"))
    if errors:
        return errors

    batch = _get(doc, "train.batch_size")
    if not isinstance(batch, int) or isinstance(batch, bool) or not (1 <= batch <= 32):
        errors.append(_issue("SVC-4001", "train.batch_size", "必须是 1–32 的整数", f"当前 {batch!r}"))

    epochs = _get(doc, "train.epochs")
    if not isinstance(epochs, int) or epochs < 1:
        errors.append(_issue("SVC-4001", "train.epochs", "必须是 ≥1 的整数", f"当前 {epochs!r}"))

    lr = _get(doc, "train.learning_rate")
    if not isinstance(lr, (int, float)) or lr <= 0 or lr > 0.01:
        errors.append(_issue("SVC-4001", "train.learning_rate", "必须在 (0, 0.01] 之间", f"当前 {lr!r}"))

    keep = _get(doc, "train.keep_ckpts")
    if not isinstance(keep, int) or keep < 0:
        errors.append(_issue("SVC-4001", "train.keep_ckpts", "必须是 ≥0 的整数", f"当前 {keep!r}"))

    for bool_key in ("train.all_in_mem", "train.fp16_run"):
        if not isinstance(_get(doc, bool_key), bool):
            errors.append(_issue("SVC-4001", bool_key, "必须是布尔值", f"当前 {_get(doc, bool_key)!r}"))

    vocoder = _get(doc, "model.vocoder_name")
    if vocoder not in VOCODERS:
        errors.append(
            _issue("SVC-4001", "model.vocoder_name", "不支持的声码器", f"{vocoder!r} ∉ {sorted(VOCODERS)}")
        )

    sr = _get(doc, "data.sampling_rate")
    if sr != 44100:
        errors.append(_issue("SVC-4001", "data.sampling_rate", "必须是 44100", f"当前 {sr!r}"))

    ssl_dim = _get(doc, "model.ssl_dim")
    encoder = _get(doc, "model.speech_encoder")
    if encoder is not _MISSING and encoder in ENCODER_DIM and ssl_dim != ENCODER_DIM[encoder]:
        errors.append(
            _issue(
                "SVC-4001",
                "model.ssl_dim",
                f"{encoder} 要求 ssl_dim={ENCODER_DIM[encoder]}",
                f"当前 {ssl_dim!r}",
            )
        )
    return errors


def validate_diffusion(doc: Dict[str, Any]) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    for key in DIFFUSION_REQUIRED:
        if _get(doc, key) is _MISSING:
            errors.append(_issue("SVC-4001", key, "缺少必需字段"))
    if errors:
        return errors

    duration = _get(doc, "data.duration")
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append(_issue("SVC-4001", "data.duration", "必须是正数（秒）", f"当前 {duration!r}"))

    batch = _get(doc, "train.batch_size")
    if not isinstance(batch, int) or batch < 1:
        errors.append(_issue("SVC-4001", "train.batch_size", "必须是 ≥1 的整数", f"当前 {batch!r}"))

    timesteps = _get(doc, "model.timesteps")
    if not isinstance(timesteps, int) or timesteps <= 0:
        errors.append(_issue("SVC-4001", "model.timesteps", "必须是正整数", f"当前 {timesteps!r}"))

    k = _get(doc, "model.k_step_max")
    if not isinstance(k, int) or k < 0:
        errors.append(_issue("SVC-4001", "model.k_step_max", "必须是 ≥0 的整数", f"当前 {k!r}"))
    elif isinstance(timesteps, int) and k >= timesteps:
        errors.append(
            _issue("SVC-4002", "model.k_step_max", "k_step_max 必须小于 timesteps", f"{k} ≥ {timesteps}")
        )

    if _get(doc, "vocoder.type") not in VOCODERS:
        errors.append(_issue("SVC-4001", "vocoder.type", "不支持的声码器", f"{_get(doc, 'vocoder.type')!r}"))
    return errors


def assert_ready(
    config: Dict[str, Any],
    diffusion: Dict[str, Any],
    min_clip_seconds: float,
    max_clip_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """10 条硬断言（附录 A.7）。返回 blocking 列表，空即通过。"""
    blocking: List[Dict[str, str]] = []
    blocking.extend(validate_config(config))
    blocking.extend(validate_diffusion(diffusion))

    duration = _get(diffusion, "data.duration")
    if isinstance(duration, (int, float)) and min_clip_seconds > 0 and duration >= min_clip_seconds:
        blocking.append(
            _issue(
                "SVC-4002",
                "data.duration",
                "扩散训练切片不能长于最短音频",
                f"duration={duration}s ≥ 最短切片 {min_clip_seconds:.2f}s",
            )
        )

    encoder = _get(config, "model.speech_encoder")
    limit = max_clip_seconds
    if limit is None and encoder in WHISPER_ENCODERS:
        limit = 25.0
    if limit is None:
        limit = 30.0
    if min_clip_seconds > limit:
        blocking.append(
            _issue(
                "SVC-4002",
                "data.duration",
                f"{encoder or '当前编码器'} 要求单条不超过 {limit:.0f}s",
                f"最短切片 {min_clip_seconds:.2f}s",
            )
        )

    spk = config.get("spk")
    if not isinstance(spk, dict) or not spk:
        blocking.append(_issue("SVC-4001", "spk", "说话人映射为空", "重新跑 preprocess_flist_config"))

    return {"passed": not blocking, "blocking": blocking}


def scan_clip_range(dataset_dir: str) -> Dict[str, float]:
    """读取 44k 数据集的最短/最长音频时长，供断言使用。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from svc_bridge.audio import read_wav  # 局部导入避免循环依赖

    durations = []
    for wav in Path(dataset_dir).glob("*/*.wav"):
        try:
            durations.append(read_wav(wav).seconds)
        except Exception:  # noqa: BLE001
            continue
    if not durations:
        return {"min": 0.0, "max": 0.0, "count": 0}
    return {"min": min(durations), "max": max(durations), "count": len(durations)}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="svc_bridge config")
    parser.add_argument("action", choices=["validate", "assert"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--diffusion", required=True)
    parser.add_argument("--dataset", default="")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        diffusion = load_diffusion(args.diffusion)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "passed": False,
            "blocking": [_issue("SVC-4001", "config", "配置无法解析", str(exc))],
        }
        sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        return 2

    if args.action == "validate":
        blocking = validate_config(config) + validate_diffusion(diffusion)
        payload = {"passed": not blocking, "blocking": blocking}
    else:
        range_ = scan_clip_range(args.dataset) if args.dataset else {"min": 0.0, "max": 0.0}
        payload = assert_ready(
            config,
            diffusion,
            float(range_["min"]),
            float(range_["max"]) if range_["max"] else None,
        )

    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0 if payload["passed"] else 2
