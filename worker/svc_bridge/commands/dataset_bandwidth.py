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
            hz = source_gate.bandwidth_db(samples, wav.sample_rate)
            if hz is None:
                failed.append(f"{spk.name}/{path.name}")
                continue
            nyquist = wav.sample_rate / 2.0
            files.append(
                {
                    "relPath": f"{spk.name}/{path.name}",
                    "seconds": round(wav.seconds, 2),
                    "rolloffHz": round(hz, 0),
                    "sampleRate": wav.sample_rate,
                    # 读数贴近 Nyquist = 采样率限制，不是素材缺高频
                    "samplingRateLimited": hz >= nyquist * 0.95,
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

    limited = [f for f in files if f.get("samplingRateLimited")]
    nyquists = sorted({int(f["sampleRate"] // 2) for f in files if f.get("sampleRate")})
    ceiling = min(20000.0, median)

    # 关键区分：多数文件读数已贴到 Nyquist，说明是「采样率限制」而非「素材缺高频」。
    # 但 44.1k/48k 容器贴 Nyquist 属于「满带宽、良好」，只有容器上限本身就低
    # （如 24 kHz → 12 kHz）才判「受采样率限制」，避免把好素材判成偏闷。
    limited_ratio = len(limited) / len(files) if files else 0.0
    if limited_ratio >= 0.5 and median < 19000:
        verdict = {
            "level": "sampling-limited",
            "label": "满带宽（受采样率限制）",
            "advice": (
                f"这批文件自身的频响已到容器的上限（采样率 {min(nyquists) * 2 // 1000}k → "
                f"Nyquist {min(nyquists)} Hz），素材在容器内是满带宽、并不闷；"
                "若要 44.1 kHz 输出，请改用 44.1 kHz 的原始文件。"
            ),
        }
    else:
        verdict = _verdict(median)

    return {
        "schema": "svcstudio.dataset.bandwidth/1",
        "files": len(files),
        "failed": failed,
        "buckets": buckets,
        "medianRolloffHz": round(median, 0),
        "minRolloffHz": round(values[0], 0),
        "maxRolloffHz": round(values[-1], 0),
        "nyquistHz": nyquists,
        # 受采样率限制的文件数：这些文件的带宽读数不能当作"素材质量"判断
        "samplingRateLimitedFiles": len(limited),
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
