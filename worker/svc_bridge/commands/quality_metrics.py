"""分离质量指标：人声/伴奏分离得干不干净。

定义（全部可标定，不用「SNR」这种音乐上没有标准定义的量）：

- bleed（旋律泄漏相关系数）= |corr(vocals, accompaniment)|，0 最好、1 最差。
  完美分离时两个 stem 无关（≈0）；没分开时两个 stem 是同一段音频（≈1）。
- vocalRatioDb = 10·log10(E_vocals / E_accompaniment)，人声相对伴奏的能量（dB）。
- mixRatioDb   = 10·log10(E_vocals / E_mix)，人声占混音的比例（dB），
  E_mix 由 E_v + E_a + 2·corr·√(E_v·E_a) 推出，不需要再解码一次原曲。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Optional

import array

from .. import audio

_EPS = 1e-12


def _centered(samples: array.array):
    np = audio._np()  # noqa: SLF001
    if np is None:
        raise RuntimeError("分离质量指标需要 numpy")
    x = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    return x - x.mean() if len(x) else x


def energy(samples: array.array) -> float:
    np = audio._np()  # noqa: SLF001
    x = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    return float((x**2).mean()) if len(x) else 0.0


def bleed_corr(vocals: array.array, accompaniment: array.array) -> Optional[float]:
    """两个 stem 的相关系数绝对值；长度按较短的一方对齐。"""
    if not vocals or not accompaniment:
        return None
    v = _centered(vocals)
    a = _centered(accompaniment)
    n = min(len(v), len(a))
    if n < 2:
        return None
    v, a = v[:n], a[:n]
    denom = math.sqrt(float((v * v).sum()) * float((a * a).sum()))
    if denom <= _EPS:
        return None
    return abs(float((v * a).sum()) / denom)


def _db(value: float) -> float:
    return 10.0 * math.log10(max(value, _EPS))


def ratio_db(numerator: float, denominator: float) -> Optional[float]:
    if numerator <= 0 or denominator <= 0:
        return None
    return round(_db(numerator / denominator), 2)


def _load(path: Path) -> array.array:
    """读任意格式（含 32-bit float WAV）——分离出的 stem 常是 float，read_wav 只吃 PCM。"""
    from . import source_gate

    wav = source_gate.decode_mono(Path(path))
    if wav is None:
        raise RuntimeError(f"无法解码：{path}")
    return audio.to_mono_if_needed(wav)


def measure(vocals_path: Path, accompaniment_path: Optional[Path]) -> Dict[str, Any]:
    """从两个 stem 文件算指标；伴奏缺失时只返回人声能量。"""
    vocals = _load(vocals_path)
    e_v = energy(vocals)
    out: Dict[str, Any] = {"vocalRmsDbfs": round(10 * math.log10(max(e_v, _EPS)), 2)}
    if accompaniment_path is None or not Path(accompaniment_path).is_file():
        out.update({"bleed": None, "vocalRatioDb": None, "mixRatioDb": None})
        return out

    accompaniment = _load(accompaniment_path)
    e_a = energy(accompaniment)
    corr = bleed_corr(vocals, accompaniment)

    out["accompRmsDbfs"] = round(10 * math.log10(max(e_a, _EPS)), 2)
    out["bleed"] = round(corr, 4) if corr is not None else None
    out["vocalRatioDb"] = ratio_db(e_v, e_a)
    if corr is not None:
        # E_mix = E_v + E_a + 2·corr·√(E_v·E_a)（用相关系数还原交叉项）
        e_mix = e_v + e_a + 2.0 * corr * math.sqrt(max(e_v, _EPS) * max(e_a, _EPS))
        out["mixRatioDb"] = ratio_db(e_v, e_mix)
    else:
        out["mixRatioDb"] = ratio_db(e_v, e_v + e_a)
    return out


def summarize(entries: list[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐文件指标汇总成中位数，供报告/界面显示。"""
    def median(key: str) -> Optional[float]:
        vals = sorted(e[key] for e in entries if isinstance(e.get(key), (int, float)))
        if not vals:
            return None
        return round(vals[len(vals) // 2], 3)

    return {
        "fileCount": len(entries),
        "medianBleed": median("bleed"),
        "medianVocalRatioDb": median("vocalRatioDb"),
        "medianMixRatioDb": median("mixRatioDb"),
        "worstBleed": max((e["bleed"] for e in entries if isinstance(e.get("bleed"), (int, float))), default=None),
    }
