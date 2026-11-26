"""已有分片的去爆音修复。

切片阶段如果只能硬切（整首歌都没有达到静音阈值的间隙），每段首尾就是从波形中间
截断的阶跃，播放/训练时表现为"咔哒"声。这里对已有分片补上 8ms 余弦淡入淡出并去
直流，只碰首尾样本，不改变内容、时长与采样格式。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audio import fade_edges, read_wav, remove_dc, write_wav


def _already_faded(wav_path: Path) -> bool:
    try:
        wav = read_wav(wav_path)
    except Exception:  # noqa: BLE001
        return False
    if not wav.samples:
        return True
    return abs(int(wav.samples[0])) <= 1 and abs(int(wav.samples[-1])) <= 1


def declick_tree(
    root: str, fade_ms: float = 8.0, backup_dir: Optional[str] = None
) -> Dict[str, Any]:
    base = Path(root)
    backup = Path(backup_dir) if backup_dir else None
    changed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for wav_path in sorted(spk.glob("*.wav")):
            rel = f"{spk.name}/{wav_path.name}"
            try:
                wav = read_wav(wav_path)
                if wav.channels != 1:
                    skipped.append({"relPath": rel, "reason": "不是单声道，跳过"})
                    continue
                if _already_faded(wav_path):
                    skipped.append({"relPath": rel, "reason": "首尾已是 0，无需处理"})
                    continue
                fixed = fade_edges(remove_dc(wav.samples), wav.sample_rate, fade_ms)
                if backup is not None:
                    dest = backup / spk.name / wav_path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copyfile(wav_path, dest)
                write_wav(wav_path, fixed, wav.sample_rate, 1)
                changed.append({"relPath": rel, "fadeMs": fade_ms})
            except Exception as exc:  # noqa: BLE001
                failed.append({"relPath": rel, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "schema": "svcstudio.dataset.declick/1",
        "fadeMs": fade_ms,
        "backupDir": str(backup) if backup else None,
        "changed": changed,
        "skipped": skipped,
        "failed": failed,
        "changedCount": len(changed),
        "skippedCount": len(skipped),
        "failedCount": len(failed),
    }
