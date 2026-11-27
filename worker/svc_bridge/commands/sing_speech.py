"""说话 vs 歌唱判别：只用 F0 的统计特征，不依赖任何模型权重。

为什么是这个特征：歌唱有「持续稳定音高」（长音、拖腔），说话的音高持续在变。
指标定义（全部可标定）：

- voicedRatio：有基频的帧占比
- f0MedianHz / f0RangeSemitones：音高中心与 p5–p95 音域（半音）
- sustainedRatio：**有基频的帧里**，处于「稳定音高持续段」的帧占比。
  稳定 = 该帧音高相对局部（±150ms）中位偏差 ≤1 半音；持续段 = 连续稳定 ≥300ms。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import array

from .. import audio

F0_MIN_HZ = 65.0
F0_MAX_HZ = 1100.0
WORK_SR = 22050  # pyin 在这个采样率上足够且快
HOP = 512


def _numpy():
    np = audio._np()  # noqa: SLF001
    if np is None:
        raise RuntimeError("F0 分析需要 numpy")
    return np


def load_mono(path: Path, target_sr: int = WORK_SR):
    np = _numpy()
    import librosa

    wav = audio.read_wav(Path(path))
    samples = audio.to_mono_if_needed(wav)
    x = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    if wav.sample_rate != target_sr:
        x = librosa.resample(x, orig_sr=wav.sample_rate, target_sr=target_sr)
    return x, target_sr


def extract_f0(x, sr: int = WORK_SR) -> Tuple[Any, Any]:
    import librosa

    f0, voiced, _ = librosa.pyin(
        x, fmin=F0_MIN_HZ, fmax=F0_MAX_HZ, sr=sr, frame_length=2048, hop_length=HOP
    )
    return f0, voiced


def sustained_ratio(
    semitones, voiced, frame_seconds: float, tol: float = 1.0, min_ms: float = 300.0, win: int = 7
) -> float:
    """有基频帧中，处于「稳定音高持续 ≥min_ms」段的占比。"""
    np = _numpy()
    if semitones is None or not len(semitones) or not voiced.any():
        return 0.0
    mask = voiced & np.isfinite(semitones)
    if not mask.any():
        return 0.0

    base = np.full_like(semitones, np.nan, dtype=np.float64)
    for i in range(len(semitones)):
        lo, hi = max(0, i - win), min(len(semitones), i + win + 1)
        vals = semitones[lo:hi][mask[lo:hi]]
        if len(vals):
            base[i] = float(np.median(vals))

    stable = mask & np.isfinite(base) & (np.abs(semitones - base) <= tol)
    need = max(1, int(round(min_ms / 1000.0 / max(frame_seconds, 1e-6))))

    good = 0
    run = 0
    for flag in stable:
        if flag:
            run += 1
        else:
            if run >= need:
                good += run
            run = 0
    if run >= need:
        good += run
    return float(good) / float(mask.sum())


def metrics_from_f0(f0, voiced, sr: int = WORK_SR, hop: int = HOP) -> Dict[str, Any]:
    np = _numpy()
    if f0 is None or not len(f0):
        return {"voicedRatio": 0.0, "f0MedianHz": 0.0, "rangeSemitones": 0.0, "sustainedRatio": 0.0}
    frame_seconds = hop / float(sr)
    mask = voiced & np.isfinite(f0) & (f0 > 0)
    semitones = np.full_like(f0, np.nan, dtype=np.float64)
    if mask.any():
        semitones[mask] = 12.0 * np.log2(f0[mask] / 440.0)
    if mask.any():
        lo = float(np.percentile(semitones[mask], 5))
        hi = float(np.percentile(semitones[mask], 95))
        median_hz = float(np.median(f0[mask]))
    else:
        lo = hi = median_hz = 0.0
    return {
        "voicedRatio": round(float(mask.mean()), 4),
        "f0MedianHz": round(median_hz, 1),
        "rangeSemitones": round(hi - lo, 2),
        "sustainedRatio": round(sustained_ratio(semitones, voiced, frame_seconds), 4),
    }


def analyze(path: Path) -> Dict[str, Any]:
    x, sr = load_mono(path)
    f0, voiced = extract_f0(x, sr)
    return metrics_from_f0(f0, voiced, sr)


# 标定记录（2026-11，两类真实语料各 100 条）：
#   说话 长音比例 中位 0.406（p10 0.141 / p90 0.661）
#   歌唱 长音比例 中位 0.843（p10 0.517 / p90 0.937）
#   AUC = 0.907；阈值 0.67 时 真阳性 80%、假阳性 6%
SUSTAINED_THRESHOLD = 0.67


def classify(metrics: Dict[str, Any], sustained_threshold: float = SUSTAINED_THRESHOLD) -> str:
    """返回 'singing' / 'speech'（阈值来自上面的标定记录）。"""
    ratio = float(metrics.get("sustainedRatio") or 0.0)
    return "singing" if ratio >= sustained_threshold else "speech"


def scan(root: str, limit: int = 0) -> Dict[str, Any]:
    """扫描数据集目录，给每个音频打说话/歌唱标签（只报告，不删文件）。"""
    base = Path(root)
    entries = []
    failed = []
    if not base.is_dir():
        return {
            "schema": "svcstudio.dataset.singing/1",
            "files": 0,
            "singing": 0,
            "speech": 0,
            "entries": [],
            "failed": [],
            "threshold": SUSTAINED_THRESHOLD,
        }

    candidates = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        candidates.extend(sorted(spk.rglob("*.wav")))
    # 也支持直接指向扁平目录
    candidates.extend(sorted(base.glob("*.wav")))

    for path in candidates:
        if limit and len(entries) >= limit:
            break
        try:
            m = analyze(path)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{path.name}: {exc}")
            continue
        label = classify(m)
        entries.append({"relPath": path.name, "label": label, **m})

    return {
        "schema": "svcstudio.dataset.singing/1",
        "files": len(entries),
        "singing": sum(1 for e in entries if e["label"] == "singing"),
        "speech": sum(1 for e in entries if e["label"] == "speech"),
        "threshold": SUSTAINED_THRESHOLD,
        "entries": entries[:500],
        "failed": failed[:50],
    }


