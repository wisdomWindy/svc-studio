"""把任意格式的原始音频转换成 so-vits-svc 需要的 44.1k wav。

上游 `resample.py` 只吃 wav，所以导入后的第一步是用 ffmpeg 解码成 wav；
原始文件会被移到 `<dataset_raw>/_original/<说话人>/` 保留，不直接删除。

**dataset_raw 里统一放 44.1k 单声道**（QC 与上游 resample.py 都按这个前提写）。
原始文件不会丢：它们被移到 `<dataset_raw>/_original/<说话人>/`，分离阶段会优先用
那份原文件（通常是立体声），这样分离器能拿到完整的空间信息，而 dataset_raw 的
单声道约定也不会被破坏。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audio import read_wav

WAV_EXTS = {".wav", ".wave"}
AUDIO_EXTS = WAV_EXTS | {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}
from ..procwin import no_window_kwargs

TARGET_SR = 44100


def find_ffmpeg() -> Optional[str]:
    override = os.environ.get("SVCSTUDIO_FFMPEG", "").strip()
    if override and Path(override).is_file():
        return override
    return shutil.which("ffmpeg")


def needs_convert(path: Path) -> bool:
    """非 wav，或 wav 但不是 44.1k 单声道，都需要转换。"""
    ext = path.suffix.lower()
    if ext not in WAV_EXTS:
        return ext in AUDIO_EXTS
    try:
        wav = read_wav(path)
    except Exception:  # noqa: BLE001
        return True
    return wav.sample_rate != TARGET_SR or wav.channels != 1


def _convert_one(ffmpeg: str, src: Path, dest: Path, channels: Optional[int] = 1) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
    ]
    if channels is not None:
        argv += ["-ac", str(channels)]
    argv += [
        "-ar",
        str(TARGET_SR),
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, **no_window_kwargs())
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError((proc.stderr or "").strip()[-500:] or "ffmpeg 转换失败")


def convert_tree(root: str) -> Dict[str, Any]:
    base = Path(root)
    ffmpeg = find_ffmpeg()
    converted: List[Dict[str, Any]] = []
    skipped: List[str] = []
    failed: List[Dict[str, str]] = []

    if ffmpeg is None:
        return {
            "schema": "svcstudio.dataset.convert/1",
            "ok": False,
            "error": "找不到 ffmpeg，无法转换音频格式",
            "converted": [],
            "skipped": [],
            "failed": [],
        }

    backup_root = base / "_original"
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for src in sorted(p for p in spk.iterdir() if p.is_file()):
            if src.suffix.lower() not in AUDIO_EXTS | WAV_EXTS:
                continue
            if not needs_convert(src):
                skipped.append(f"{spk.name}/{src.name}")
                continue
            dest = spk / f"{src.stem}.wav"
            tmp = spk / f".{src.stem}.converting.wav"
            try:
                _convert_one(ffmpeg, src, tmp)
                # 原文件先备份（含非 wav 与需要重采样的 wav），再替换
                backup_dir = backup_root / spk.name
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / src.name
                if not backup.exists():
                    shutil.move(str(src), str(backup))
                elif src.exists():
                    src.unlink()
                tmp.replace(dest)
                converted.append(
                    {
                        "relPath": f"{spk.name}/{src.name}",
                        "output": f"{spk.name}/{dest.name}",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                failed.append({"relPath": f"{spk.name}/{src.name}", "error": str(exc)})

    return {
        "schema": "svcstudio.dataset.convert/1",
        "ok": not failed,
        "targetSampleRate": TARGET_SR,
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
    }
