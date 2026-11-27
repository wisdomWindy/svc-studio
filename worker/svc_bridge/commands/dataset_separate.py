"""人声分离：优先 UVR5 MDX23C（`vocal_mix.separate_song`），失败再回退 Demucs。

so-vits-svc 本身不含分离能力，官方做法就是先用 UVR5 分离再训练；这里内置
MDX23C 权重（随安装包分发）与 Demucs 兜底，导入后自动跑，用户不需要额外装工具。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import quality_metrics, source_gate

WAV_EXTS = {".wav", ".wave"}
AUDIO_EXTS = WAV_EXTS | {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}


def _prepare_torch_home() -> Optional[Path]:
    """把 TORCH_HOME 指向内置运行时里的缓存目录，避免联网拉权重。"""
    runtime_root = Path(sys.executable).parent
    candidate = runtime_root / "models" / "torch"
    if (candidate / "hub" / "checkpoints").is_dir():
        os.environ.setdefault("TORCH_HOME", str(candidate))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        return candidate
    return None


def _pick_device(requested: str = "") -> str:
    if requested:
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _ensure_pcm16(path: Path) -> None:
    """把分离结果就地转成 16-bit PCM。

    MDX23C 输出的是 32-bit float WAV，而下游切片用的 `read_wav` 只支持 16-bit PCM，
    不转的话「分离成功、切片全失败」。Demucs 本来就是 PCM_16，这里会直接跳过。
    声道数与采样率保持不变（立体声要留给切片自己做降混）。
    """
    try:
        import soundfile as sf
    except Exception:  # noqa: BLE001 - 缺 soundfile 时退回 ffmpeg
        sf = None  # type: ignore[assignment]

    if sf is not None:
        try:
            if sf.info(str(path)).subtype == "PCM_16":
                return
            import numpy as np

            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            tmp = path.with_name(path.stem + ".pcm16.wav")
            sf.write(str(tmp), np.clip(data, -1.0, 1.0), sr, subtype="PCM_16")
            tmp.replace(path)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[separate] 转 16-bit 失败，尝试 ffmpeg：{exc}", file=sys.stderr)

    from .dataset_convert import _convert_one, find_ffmpeg

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("无法把分离结果转成 16-bit PCM：既没有 soundfile 也没有 ffmpeg")
    tmp = path.with_name(path.stem + ".pcm16.wav")
    _convert_one(ffmpeg, path, tmp)
    tmp.replace(path)


def _original_for(base: Path, spk: Path, src: Path) -> Path:
    """优先返回 `_original/<说话人>/` 里的原始文件（通常是立体声）。

    dataset_raw 里放的是 44.1k 单声道副本（QC 与上游都按这个前提），但分离模型
    是按立体声训练的，用单声道会丢掉区分人声/伴奏的空间信息。原始文件在导入时
    被保留在 `_original/` 下，这里按同名 stem 找回来；找不到就用单声道副本。
    """
    backup_dir = base / "_original" / spk.name
    if backup_dir.is_dir():
        candidates = [
            c
            for c in sorted(backup_dir.iterdir())
            if c.is_file() and c.stem == src.stem and c.suffix.lower() in AUDIO_EXTS
        ]
        # 同 stem 可能同时存在「原始 mp3」和「分离后挪进来的单声道 wav」：优先非 wav 的原件
        non_wav = [c for c in candidates if c.suffix.lower() not in {".wav", ".wave"}]
        if non_wav:
            return non_wav[0]
        if candidates:
            return candidates[0]
    return src


def separate_tree(
    root: str,
    device: str = "",
    model: str = "htdemucs",
    drop_rejected: bool = True,
) -> Dict[str, Any]:
    base = Path(root)
    torch_home = _prepare_torch_home()
    started = time.time()

    # 优先走 MDX23C；separate_song 内部已实现「MDX 失败自动回退 Demucs」
    from .vocal_mix import separate_song

    separated: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    # 分离前体检：明显不可用的源文件直接跳过（省一次 GPU），并如实记录原因
    skipped: List[Dict[str, Any]] = []
    backup_root = base / "_original"
    engines: Dict[str, int] = {}

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for src in sorted(p for p in spk.iterdir() if p.is_file()):
            ext = src.suffix.lower()
            if ext not in AUDIO_EXTS and ext not in WAV_EXTS:
                continue
            # 跳过所有已分离产物：`x.vocals.wav`（整段干声）与 `x.vocals_001.wav`（切片）。
            # 否则对已经处理过的数据集再点一次「自动分离」，会把切片当成原曲重跑一遍。
            if ".vocals" in src.stem:
                continue
            dest = spk / f"{src.stem}.vocals.wav"
            if dest.exists():
                continue
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    sep_src = _original_for(base, spk, src)
                    metrics = source_gate.analyze(sep_src)
                    reason = source_gate.gate_reason(metrics)
                    if reason:
                        skipped.append(
                            {
                                "relPath": f"{spk.name}/{src.name}",
                                "reason": reason,
                                "deleted": drop_rejected,
                                **{k: v for k, v in metrics.items() if k != "readable"},
                            }
                        )
                        if drop_rejected:
                            src.unlink(missing_ok=True)
                        continue
                    result = separate_song(sep_src, Path(tmp), device=device, model=model)
                    vocals = Path(str(result.get("vocals") or ""))
                    if not vocals.is_file():
                        raise RuntimeError(f"分离结果缺失：{result}")
                    _ensure_pcm16(vocals)
                    quiet = source_gate.vocal_too_quiet(vocals)
                    if quiet is not None:
                        skipped.append(
                            {
                                "relPath": f"{spk.name}/{src.name}",
                                "reason": f"分离后干声几乎为空（{quiet:.1f} dBFS）",
                                "deleted": drop_rejected,
                            }
                        )
                        if drop_rejected:
                            src.unlink(missing_ok=True)
                        continue
                    # P1：分离质量指标（只报告，不参与删除）
                    quality = quality_metrics.measure(
                        vocals, Path(str(result.get("accompaniment") or "")) if result.get("accompaniment") else None
                    )
                    shutil.copyfile(vocals, dest)
                    engine = str(result.get("engine") or "unknown")
                    engines[engine] = engines.get(engine, 0) + 1
                backup_dir = backup_root / spk.name
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / src.name
                if not backup.exists():
                    shutil.move(str(src), str(backup))
                else:
                    src.unlink(missing_ok=True)
                separated.append(
                    {
                        "relPath": f"{spk.name}/{src.name}",
                        "output": f"{spk.name}/{dest.name}",
                        "engine": engine,
                        "quality": quality,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"relPath": f"{spk.name}/{src.name}", "error": str(exc)})

    return {
        "schema": "svcstudio.dataset.separate/1",
        "ok": not failed,
        "model": model,
        "device": device or _pick_device(device),
        "engines": engines,
        "offlineWeights": str(torch_home) if torch_home else None,
        "seconds": round(time.time() - started, 1),
        "separated": separated,
        # 分离质量汇总：只报告，不参与删除（阈值待标定后由使用方决定）
        "quality": quality_metrics.summarize([s.get("quality") or {} for s in separated]),
        "skipped": skipped,
        "failed": failed,
    }
