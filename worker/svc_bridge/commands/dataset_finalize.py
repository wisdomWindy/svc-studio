"""收尾：项目里只保留最终的干声片段。

之前的中间产物（原始文件、分离出的整段干声、转换临时文件、quarantine 目录）
在切片成功后一律删除，避免项目目录里堆积无用数据。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from ..audio import read_wav

TARGET_SR = 44100
MAX_SECONDS = 15.5  # 切片上限 15s，留一点浮点余量


def _is_final(path: Path) -> bool:
    if path.suffix.lower() not in {".wav", ".wave"}:
        return False
    try:
        wav = read_wav(path)
    except Exception:  # noqa: BLE001
        return False
    return wav.sample_rate == TARGET_SR and wav.channels == 1 and 0 < wav.seconds <= MAX_SECONDS


def finalize_tree(root: str) -> Dict[str, Any]:
    base = Path(root)
    removed: List[str] = []
    kept: List[str] = []

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for entry in sorted(spk.iterdir()):
            if entry.is_file():
                if _is_final(entry):
                    kept.append(f"{spk.name}/{entry.name}")
                else:
                    entry.unlink(missing_ok=True)
                    removed.append(f"{spk.name}/{entry.name}")
            elif entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(f"{spk.name}/{entry.name}/")

    for internal in ("_original", "_tmp"):
        target = base / internal
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(f"{internal}/")

    quarantine = base.parent / "quarantine"
    if quarantine.exists():
        shutil.rmtree(quarantine, ignore_errors=True)
        removed.append("quarantine/")

    return {
        "schema": "svcstudio.dataset.finalize/1",
        "kept": kept,
        "removed": removed,
        "keptCount": len(kept),
    }
