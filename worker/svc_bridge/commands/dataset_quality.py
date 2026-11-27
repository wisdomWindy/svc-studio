"""按分离质量筛选片段。

分离残留是训练集的"平均质量"决定因素：把最差的那批留在里面，等于用低信噪比数据
稀释音色。这里用「信噪比代理」给每个片段打分——以最安静的一成帧当作噪声底，与整体
RMS 相比——然后剔除最差的一批。

被剔除的文件**移动到备份目录**（默认 `<root>/../_pruned/<说话人>/`），不直接删除。
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..audio import read_wav, to_mono_if_needed

FRAME_SECONDS = 0.05
SILENCE_PERCENTILE = 0.10


def _frame_rms(samples, frame_len: int) -> List[float]:
    """每帧 RMS（优先用 numpy；没有就退回纯 Python）。"""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        out: List[float] = []
        for start in range(0, len(samples) - frame_len + 1, frame_len):
            chunk = samples[start : start + frame_len]
            acc = 0.0
            for s in chunk:
                acc += float(s) * float(s)
            out.append(math.sqrt(acc / len(chunk)) / 32768.0)
        return out

    arr = np.asarray(samples, dtype=np.float32) / 32768.0
    n = len(arr) // frame_len
    if n <= 0:
        return []
    frames = arr[: n * frame_len].reshape(n, frame_len)
    return (np.sqrt((frames**2).mean(axis=1))).tolist()


def score_file(path: Path) -> Dict[str, Any]:
    """给一个片段打分：信噪比代理、RMS、时长。解码失败则标记 unreadable。"""
    try:
        wav = read_wav(path)
    except Exception as exc:  # noqa: BLE001
        return {"relPath": path.name, "readable": False, "error": str(exc)}
    mono = to_mono_if_needed(wav)
    frame_len = max(1, int(wav.sample_rate * FRAME_SECONDS))
    frames = _frame_rms(mono, frame_len)
    if not frames:
        return {"relPath": path.name, "readable": False, "error": "太短，无法分帧"}
    ordered = sorted(frames)
    idx = max(0, int(len(ordered) * SILENCE_PERCENTILE) - 1)
    floor = ordered[idx]
    overall = math.sqrt(sum(f * f for f in frames) / len(frames))
    snr = 20 * math.log10(overall / floor) if floor > 0 and overall > 0 else 0.0
    return {
        "relPath": path.name,
        "readable": True,
        "seconds": round(wav.seconds, 3),
        "snrDb": round(snr, 2),
        "rmsDbfs": round(20 * math.log10(max(overall, 1e-12)), 2),
    }


def prune_quality_tree(
    root: str,
    keep: float = 0.85,
    backup_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """按 SNR 代理从差到好排序，剔除最差的 (1-keep) 比例。

    默认**直接删除**不合格片段（只保留合格音频）；
    传 `backup_dir` 时改为移动到该目录，便于排障回滚。
    """
    base = Path(root)
    keep = min(max(keep, 0.1), 1.0)
    backup = Path(backup_dir) if backup_dir else None

    scored: List[Dict[str, Any]] = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for wav_path in sorted(spk.glob("*.wav")):
            entry = score_file(wav_path)
            entry["speaker"] = spk.name
            entry["path"] = wav_path
            scored.append(entry)

    readable = [e for e in scored if e["readable"]]
    dropped: List[Dict[str, Any]] = []
    kept = len(readable)
    if readable:
        readable.sort(key=lambda e: e["snrDb"])
        drop_count = int(round(len(readable) * (1.0 - keep)))
        for entry in readable[:drop_count]:
            # 默认直接删除（产品要求：只保留合格音频，不留备份副本）
            # 传了 backup_dir 才改为移动到备份目录，便于手工排障时回滚
            if backup is None:
                entry["path"].unlink(missing_ok=True)
                dropped.append(
                    {
                        "relPath": f"{entry['speaker']}/{entry['path'].name}",
                        "snrDb": entry["snrDb"],
                        "deleted": True,
                    }
                )
            else:
                dest = backup / entry["speaker"] / entry["path"].name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry["path"]), str(dest))
                dropped.append(
                    {
                        "relPath": f"{entry['speaker']}/{entry['path'].name}",
                        "snrDb": entry["snrDb"],
                        "deleted": False,
                        "to": str(dest),
                    }
                )
        kept -= len(dropped)

    return {
        "schema": "svcstudio.dataset.prune-quality/1",
        "keepRatio": keep,
        "totalFiles": len(scored),
        "readableFiles": len(readable),
        "keptFiles": kept,
        "droppedFiles": len(dropped),
        "backupDir": str(backup) if backup is not None else None,
        "deletedFiles": sum(1 for d in dropped if d.get("deleted")),
        "snrThresholdDb": round(dropped[-1]["snrDb"], 2) if dropped else None,
        "scores": [
            {k: v for k, v in e.items() if k != "path"} for e in readable
        ],
        "dropped": dropped,
        "unreadable": [
            {"relPath": e["relPath"], "error": e.get("error")} for e in scored if not e["readable"]
        ],
    }
