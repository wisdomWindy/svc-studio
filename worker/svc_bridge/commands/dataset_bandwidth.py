"""导入前的带宽体检：估算这批素材能支撑多亮的模型输出。

原理：so-vits-svc 的输出由 mel 频谱决定，而 mel 的上限不可能超过训练素材的高频上限。
所以「素材 99% 滚降」就是模型音色亮度的**上界**——先告诉用户，别等训练完才发现偏闷。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import audio
from . import source_gate

BUCKETS = [
    (0, 12000, "≤12 kHz"),
    (12000, 14000, "12–14 kHz"),
    (14000, 16000, "14–16 kHz"),
    (16000, 19000, "16–19 kHz"),
    (19000, 100000, "≥19 kHz"),
]

GOOD_HZ = 19000  # 接近 CD 级上限，输出可"亮"
OK_HZ = 16000  # 可接受，但会略闷
DULL_HZ = 14000  # 明显偏闷


def _bucket(hz: float) -> str:
    for lo, hi, label in BUCKETS:
        if lo <= hz < hi:
            return label
    return BUCKETS[-1][2]


def _verdict(median_hz: float) -> Dict[str, str]:
    if median_hz >= GOOD_HZ:
        return {
            "level": "good",
            "label": "良好",
            "advice": "素材高频完整，模型可以输出明亮的音色。",
        }
    if median_hz >= OK_HZ:
        return {
            "level": "ok",
            "label": "可接受",
            "advice": "高频略有损失，输出会比原唱稍闷，属可训练素材。",
        }
    if median_hz >= DULL_HZ:
        return {
            "level": "dull",
            "label": "偏闷",
            "advice": "素材高频被截断（常见于二次转码），模型输出会明显偏闷；"
            "建议换无损或 99% 滚降 ≥16 kHz 的源文件。",
        }
    return {
        "level": "very-dull",
        "label": "明显偏闷",
        "advice": "素材带宽极窄（多来自低码率转码），输出会比较糊；强烈建议更换源文件。",
    }


def scan(root: str, limit: int = 0) -> Dict[str, Any]:
    """扫描 dataset_raw/<说话人>/ 下的源文件，返回带宽分布与输出上限预估。"""
    base = Path(root)
    files: List[Dict[str, Any]] = []
    failed: List[str] = []

    if not base.is_dir():
        return {
            "schema": "svcstudio.dataset.bandwidth/1",
            "files": 0,
            "failed": [],
            "buckets": {},
            "medianRolloffHz": None,
            "estimatedOutputCeilingHz": None,
            "verdict": _verdict(0.0) | {"label": "无素材"},
            "note": "数据集目录为空",
        }

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for path in sorted(spk.iterdir()):
            if not path.is_file() or path.suffix.lower() not in source_gate_supported():
                continue
            wav = source_gate.decode_mono(path)
            if wav is None:
                failed.append(f"{spk.name}/{path.name}")
                continue
            samples = audio.to_mono_if_needed(wav)
            hz = source_gate._rolloff_hz(samples, wav.sample_rate)  # noqa: SLF001
            if hz is None:
                failed.append(f"{spk.name}/{path.name}")
                continue
            files.append(
                {
                    "relPath": f"{spk.name}/{path.name}",
                    "seconds": round(wav.seconds, 2),
                    "rolloffHz": round(hz, 0),
                }
            )
            if limit and len(files) >= limit:
                break

    if not files:
        result = {
            "schema": "svcstudio.dataset.bandwidth/1",
            "files": 0,
            "failed": failed,
            "buckets": {},
            "medianRolloffHz": None,
            "estimatedOutputCeilingHz": None,
            "verdict": _verdict(0.0) | {"label": "无素材"},
            "note": "没有可解码的音频文件",
        }
        return result

    values = sorted(f["rolloffHz"] for f in files)
    median = values[len(values) // 2]
    buckets: Dict[str, int] = {}
    for f in files:
        buckets[_bucket(f["rolloffHz"])] = buckets.get(_bucket(f["rolloffHz"]), 0) + 1

    ceiling = min(20000.0, median)
    verdict = _verdict(median)
    return {
        "schema": "svcstudio.dataset.bandwidth/1",
        "files": len(files),
        "failed": failed,
        "buckets": buckets,
        "medianRolloffHz": round(median, 0),
        "minRolloffHz": round(values[0], 0),
        "maxRolloffHz": round(values[-1], 0),
        # 素材上限即输出上限（mel 不可能凭空补回缺失的高频）
        "estimatedOutputCeilingHz": round(ceiling, 0),
        "verdict": verdict,
        "seconds": round(sum(f["seconds"] for f in files), 1),
    }


def source_gate_supported() -> set:
    return {
        ".wav",
        ".wave",
        ".mp3",
        ".m4a",
        ".flac",
        ".aac",
        ".ogg",
        ".opus",
        ".wma",
    }
