"""数据集精修：按「可测量的质量指标」筛选 + 无损归一化，只保留合格切片。

与 prune-quality 的区别：
- prune-quality 按比例剔尾（固定丢 10%），不看绝对标准；
- refine 按**绝对阈值**判合格（时长/噪声底/带宽），去近重复，并把合格切片响度归一化。
  不合格的直接删除（产品要求：只保留符合条件的高质量音频）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import array

from .. import audio

DEFAULT_MIN_SECONDS = 5.0
DEFAULT_MAX_SECONDS = 15.0
# 下面两项默认关闭：它们都不能可靠地衡量「素材质量」，只作为可选筛选项保留。
# - 干声带宽取决于分离器残留（同源 RoFormer 11kHz / Demucs 22kHz）
# - quietFrameRatio 是动态范围代理，20dB 以上饱和，不是 SNR
DEFAULT_MIN_SNR_DB = 0.0
DEFAULT_MIN_ROLLOFF_HZ = 0.0  # 0 = 不按带宽筛
DEFAULT_DEDUP_SIMILARITY = 0.995
DEFAULT_TARGET_RMS_DBFS = -20.0
DEFAULT_PEAK_DBFS = -1.0
FINGERPRINT_BANDS = 16


def _fingerprint(x, sr: int, np) -> Optional["np.ndarray"]:
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    edges = np.geomspace(100, 16000, FINGERPRINT_BANDS + 1)
    fp = np.array([spec[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in zip(edges[:-1], edges[1:])])
    total = float(fp.sum())
    if total <= 0:
        return None
    fp = fp / total
    norm = float(np.linalg.norm(fp))
    return fp / norm if norm > 0 else None


def _measure(path: Path, np) -> Dict[str, Any]:
    from . import source_gate

    wav = audio.read_wav(path)
    samples = audio.to_mono_if_needed(wav)
    x = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    sr = wav.sample_rate
    # 带宽必须用与 Layer-1 相同的标定口径（-50dB 上限），否则报告自相矛盾
    bw = source_gate.bandwidth_db(samples, sr) or 0.0
    fl = max(1, int(sr * 0.01))
    n = len(x) // fl
    frames = np.sqrt(((x[: n * fl].reshape(n, fl)) ** 2).mean(axis=1)) if n else np.array([0.0])
    floor = float(np.percentile(frames, 10))
    rms = float(np.sqrt((x**2).mean()))
    # 诚实命名：这是「最安静 10% 帧 vs 整体」的动态范围代理，不是 SNR
    quiet_ratio = 20 * math.log10(rms / floor) if floor > 1e-9 else 60.0
    return {
        "path": path,
        "seconds": round(wav.seconds, 3),
        "quietFrameRatioDb": round(quiet_ratio, 2),
        "rmsDbfs": round(20 * math.log10(max(rms, 1e-12)), 2),
        "rolloffHz": round(bw, 0),
        "clipRatio": round(audio.clipping_ratio(samples), 6),
        "samples": samples,
        "sampleRate": sr,
        "fingerprint": _fingerprint(x, sr, np),
    }


def _normalize(samples: array.array, np, target_dbfs: float, peak_dbfs: float) -> array.array:
    """把 RMS 拉到目标值，同时保证峰值不超过 ceiling；不放大到削波。"""
    arr = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt((arr**2).mean()))
    if rms <= 1e-9:
        return samples
    gain = 10 ** (target_dbfs / 20.0) / rms
    ceiling = 10 ** (peak_dbfs / 20.0)
    peak = float(np.abs(arr).max())
    if peak * gain > ceiling:
        gain = ceiling / peak
    out = np.clip(arr * gain, -1.0, 1.0) * 32767.0
    return array.array("h", np.trunc(out).astype("<i2").tobytes())


def refine_tree(
    root: str,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    min_snr_db: float = DEFAULT_MIN_SNR_DB,
    min_rolloff_hz: float = DEFAULT_MIN_ROLLOFF_HZ,
    dedup_similarity: float = DEFAULT_DEDUP_SIMILARITY,
    target_rms_dbfs: float = DEFAULT_TARGET_RMS_DBFS,
    peak_dbfs: float = DEFAULT_PEAK_DBFS,
    normalize: bool = True,
) -> Dict[str, Any]:
    base = Path(root)
    np = audio._np()  # noqa: SLF001 - 同包内部约定
    if np is None:
        raise RuntimeError("refine 需要 numpy（随运行时提供）")

    scored: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for wav_path in sorted(spk.glob("*.wav")):
            try:
                m = _measure(wav_path, np)
            except Exception as exc:  # noqa: BLE001
                unreadable.append(f"{spk.name}/{wav_path.name}: {exc}")
                continue
            m["speaker"] = spk.name
            m["relPath"] = f"{spk.name}/{wav_path.name}"
            scored.append(m)

    rejected: List[Dict[str, Any]] = []

    def reject(entry: Dict[str, Any], reason: str) -> None:
        rejected.append({"relPath": entry["relPath"], "reason": reason, "quietFrameRatioDb": entry["quietFrameRatioDb"]})
        entry["path"].unlink(missing_ok=True)

    # 1) 绝对阈值
    survivors: List[Dict[str, Any]] = []
    for entry in scored:
        if entry["seconds"] < min_seconds:
            reject(entry, f"时长 {entry['seconds']:.2f}s < {min_seconds:.0f}s")
        elif entry["seconds"] > max_seconds:
            reject(entry, f"时长 {entry['seconds']:.2f}s > {max_seconds:.0f}s")
        elif entry["clipRatio"] >= 0.001:
            reject(entry, f"削波 {entry['clipRatio'] * 100:.2f}%")
        elif entry["quietFrameRatioDb"] < min_snr_db:
            reject(entry, f"静音段动态范围 {entry['quietFrameRatioDb']:.1f}dB < {min_snr_db:.0f}dB")
        elif min_rolloff_hz > 0 and entry["rolloffHz"] < min_rolloff_hz:
            reject(entry, f"带宽 {entry['rolloffHz']:.0f}Hz < {min_rolloff_hz:.0f}Hz")
        else:
            survivors.append(entry)

    # 2) 近重复：按质量从好到差贪心保留，重复的删除
    deduped: List[Dict[str, Any]] = []
    duplicates = 0
    if dedup_similarity > 0 and len(survivors) > 1:
        survivors.sort(key=lambda e: e["quietFrameRatioDb"], reverse=True)
        kept_fp: List[Any] = []
        for entry in survivors:
            fp = entry["fingerprint"]
            if fp is not None and kept_fp:
                mat = np.stack(kept_fp)
                sim = float((mat @ fp).max())
                if sim >= dedup_similarity:
                    reject(entry, f"近重复（相似度 {sim:.4f}）")
                    duplicates += 1
                    continue
            if fp is not None:
                kept_fp.append(fp)
            deduped.append(entry)
        # 恢复稳定顺序
        deduped.sort(key=lambda e: e["relPath"])
    else:
        deduped = survivors

    # 3) 合格切片做响度归一化（无损，不丢数据）
    normalized = 0
    rms_after: List[float] = []
    if normalize:
        for entry in deduped:
            out = _normalize(entry["samples"], np, target_rms_dbfs, peak_dbfs)
            if len(out) == len(entry["samples"]):
                audio.write_wav(entry["path"], out, entry["sampleRate"], 1)
                normalized += 1
            arr = np.frombuffer(out.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
            rms_after.append(round(20 * math.log10(max(float(np.sqrt((arr**2).mean())), 1e-12)), 1))
    else:
        rms_after = [entry["rmsDbfs"] for entry in deduped]

    return {
        "schema": "svcstudio.dataset.refine/1",
        "inputFiles": len(scored),
        "keptFiles": len(deduped),
        "rejectedFiles": len(rejected),
        "rejectedUnreadable": unreadable,
        "duplicates": duplicates,
        "normalized": normalized,
        "thresholds": {
            "minSeconds": min_seconds,
            "maxSeconds": max_seconds,
            "minSnrDb": min_snr_db,
            "minRolloffHz": min_rolloff_hz,
            "dedupSimilarity": dedup_similarity,
            "targetRmsDbfs": target_rms_dbfs,
            "peakDbfs": peak_dbfs,
        },
        "keptStats": {
            "rolloffHzMedian": round(float(np.median([e["rolloffHz"] for e in deduped])), 0) if deduped else None,
            "quietFrameRatioDbMedian": round(float(np.median([e["quietFrameRatioDb"] for e in deduped])), 1) if deduped else None,
            "rmsDbfsMedian": round(float(np.median(rms_after)), 1) if rms_after else None,
            "secondsTotal": round(sum(e["seconds"] for e in deduped), 1),
        },
        "rejected": rejected[:200],
    }
