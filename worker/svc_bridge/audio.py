"""纯标准库音频原语（16-bit PCM WAV）。

刻意不依赖 numpy/librosa：QC 与切片必须能在"只有内置运行时"的机器上跑，
而且在测试里可以用合成波形精确断言。生产环境中真正吃算力的推理/训练才用 torch。
"""

from __future__ import annotations

import array
import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

SILENCE_DBFS = -40.0
SILENCE_LINEAR = 10 ** (SILENCE_DBFS / 20.0)
CLIP_THRESHOLD = 0.999
CLIP_MIN_RUN = 3

# numpy 只是加速手段：没有它也能跑（纯标准库路径）。设 SVCSTUDIO_NO_NUMPY=1 可强制走慢路径，
# 用于在真实数据上核对两条路径的输出是否逐字节一致。
_NUMPY = None
_NUMPY_TRIED = False


def _np():
    global _NUMPY, _NUMPY_TRIED
    if not _NUMPY_TRIED:
        _NUMPY_TRIED = True
        try:
            import os

            if not os.environ.get("SVCSTUDIO_NO_NUMPY"):
                import numpy

                _NUMPY = numpy
        except Exception:  # noqa: BLE001
            _NUMPY = None
    return _NUMPY


def _to_float(samples: array.array):
    np = _np()
    return np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64)


def _from_float(arr, np) -> array.array:
    clipped = np.clip(arr, -32768.0, 32767.0)
    return array.array("h", np.trunc(clipped).astype("<i2").tobytes())


@dataclass
class Wav:
    sample_rate: int
    channels: int
    samples: array.array  # 交错 16-bit
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / float(self.sample_rate) if self.sample_rate else 0.0

    def mono(self) -> array.array:
        if self.channels == 1:
            return self.samples
        np = _np()
        if np is not None and len(self.samples) % self.channels == 0:
            arr = np.frombuffer(self.samples.tobytes(), dtype="<i2").reshape(-1, self.channels)
            # 与原实现一致：先按通道求均值，再向零取整
            return array.array("h", np.trunc(arr.mean(axis=1)).astype("<i2").tobytes())
        out = array.array("h")
        n = self.channels
        for i in range(0, len(self.samples) - n + 1, n):
            out.append(int(sum(self.samples[i : i + n]) / n))
        return out


def read_wav(path: Path) -> Wav:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.getnframes()
        raw = wf.readframes(frames)
    if width != 2:
        raise ValueError(f"只支持 16-bit PCM，当前 {width * 8}-bit: {path}")
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        _swap_to_native(samples)
    return Wav(sample_rate=rate, channels=channels, samples=samples, frames=frames)


def _swap_to_native(samples: array.array) -> None:
    # wave 返回小端；x86 上 array('h') 也是小端，无需处理，保留钩子便于跨平台。
    pass


def write_wav(path: Path, samples: array.array, sample_rate: int, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


def frame_rms(samples: array.array, frame_len: int) -> List[float]:
    np = _np()
    if np is not None and frame_len > 0:
        arr = _to_float(samples) / 32768.0
        n = len(arr) // frame_len
        if n <= 0:
            return []
        frames = arr[: n * frame_len].reshape(n, frame_len)
        return np.sqrt((frames**2).mean(axis=1)).tolist()
    out: List[float] = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        chunk = samples[start : start + frame_len]
        acc = 0
        for s in chunk:
            acc += float(s) * float(s)
        out.append(math.sqrt(acc / len(chunk)) / 32768.0)
    return out


def rms_dbfs(samples: array.array) -> float:
    if not samples:
        return -120.0
    np = _np()
    if np is not None:
        arr = _to_float(samples)
        rms = math.sqrt(float((arr**2).mean())) / 32768.0
        return 20.0 * math.log10(max(rms, 1e-12))
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    rms = math.sqrt(acc / len(samples)) / 32768.0
    return 20.0 * math.log10(max(rms, 1e-12))


def peak_dbfs(samples: array.array) -> float:
    if not samples:
        return -120.0
    np = _np()
    if np is not None:
        peak = float(np.abs(_to_float(samples)).max()) / 32768.0
        return 20.0 * math.log10(max(peak, 1e-12))
    peak = max(abs(int(s)) for s in samples) / 32768.0
    return 20.0 * math.log10(max(peak, 1e-12))


def silence_ratio(samples: array.array, sample_rate: int, frame_ms: int = 10) -> float:
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    frames = frame_rms(samples, frame_len)
    if not frames:
        return 0.0
    silent = sum(1 for r in frames if r < SILENCE_LINEAR)
    return silent / len(frames)


def clipping_ratio(samples: array.array) -> float:
    if not samples:
        return 0.0
    limit = int(32767 * CLIP_THRESHOLD)
    np = _np()
    if np is not None:
        hot = np.abs(_to_float(samples)) >= limit
        if not hot.any():
            return 0.0
        edges = np.diff(np.concatenate(([0], hot.astype(np.int8), [0])))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)
        clipped = int(sum(e - s for s, e in zip(starts, ends) if e - s >= CLIP_MIN_RUN))
        return clipped / len(samples)
    clipped = 0
    run = 0
    total = len(samples)
    for s in samples:
        if abs(int(s)) >= limit:
            run += 1
        else:
            if run >= CLIP_MIN_RUN:
                clipped += run
            run = 0
    if run >= CLIP_MIN_RUN:
        clipped += run
    return clipped / total


def trim_silence(samples: array.array, sample_rate: int, margin_ms: int = 50) -> array.array:
    frame_len = max(1, int(sample_rate * 0.01))
    margin = max(1, int(sample_rate * margin_ms / 1000))
    frames = frame_rms(samples, frame_len)
    loud = [i for i, r in enumerate(frames) if r >= SILENCE_LINEAR]
    if not loud:
        return samples
    start = max(0, loud[0] * frame_len - margin)
    end = min(len(samples), (loud[-1] + 1) * frame_len + margin)
    return samples[start:end]


def resample_linear(samples: array.array, src_rate: int, dst_rate: int) -> array.array:
    if src_rate == dst_rate or not samples:
        return samples
    ratio = dst_rate / float(src_rate)
    out_len = int(len(samples) * ratio)
    np = _np()
    if np is not None and out_len > 0:
        arr = _to_float(samples)
        last = len(arr) - 1
        pos = np.arange(out_len, dtype=np.float64) / ratio
        left = np.floor(pos).astype(np.int64)
        frac = pos - left
        left = np.minimum(left, last)
        right = np.minimum(left + 1, last)
        out = arr[left] + (arr[right] - arr[left]) * frac
        return _from_float(out, np)
    out = array.array("h", bytes(2 * out_len))
    last = len(samples) - 1
    for i in range(out_len):
        pos = i / ratio
        left = int(pos)
        frac = pos - left
        a = samples[min(left, last)]
        b = samples[min(left + 1, last)]
        out[i] = int(a + (b - a) * frac)
    return out


def fade_edges(samples: array.array, sample_rate: int, fade_ms: float = 8.0) -> array.array:
    """首尾各做一段余弦淡入淡出，消掉硬切产生的"咔哒"爆音。

    8ms 在听感上看不出起音变化，但足以让波形从 0 平滑进入/退出，避免切口处的
    阶跃被扬声器还原成爆音。内部样本一个不动。
    """
    n = len(samples)
    fade = min(int(sample_rate * fade_ms / 1000.0), n // 2)
    if n == 0 or fade <= 0:
        return samples
    out = array.array("h", samples)
    for i in range(fade):
        gain = 0.5 - 0.5 * math.cos(math.pi * (i + 0.5) / fade)
        out[i] = int(out[i] * gain)
        out[n - 1 - i] = int(out[n - 1 - i] * gain)
    return out


def remove_dc(samples: array.array, min_offset: float = 0.5) -> array.array:
    """去掉直流偏置（低于 0.5 LSB 的偏移忽略，避免无谓改动）。"""
    if not samples:
        return samples
    np = _np()
    if np is not None:
        arr = _to_float(samples)
        mean = float(arr.mean())
        if abs(mean) < min_offset:
            return samples
        return _from_float(arr - mean, np)
    mean = sum(samples) / float(len(samples))
    if abs(mean) < min_offset:
        return samples
    return array.array("h", (max(-32768, min(32767, int(s - mean))) for s in samples))


def find_quietest_cut(
    frames: List[float], start_frame: int, min_frames: int, max_frames: int, frame_len: int
) -> Tuple[int, int]:
    """在 [start+min, max] 内挑能量最低的一帧作为切点。

    优先落在静音帧里；整段都没有静音时退而取窗口内最安静的帧——比"硬切在固定
    时长处"爆音小得多，也不会一刀切在元音中间。
    """
    lower = min(start_frame + min_frames, len(frames))
    upper = min(max_frames, len(frames))
    if upper <= lower:
        return upper, upper * frame_len
    window = range(lower, upper)
    quiet = [i for i in window if frames[i] < SILENCE_LINEAR]
    pool = quiet if quiet else list(window)
    best = min(pool, key=lambda i: frames[i])
    return best, best * frame_len


def to_mono_if_needed(wav: Wav) -> array.array:
    return wav.mono() if wav.channels > 1 else wav.samples


def protect_peak(samples: array.array, max_dbfs: float = -1.0) -> array.array:
    """把峰值压到 max_dbfs，替代上游"归一化到 0dBFS"的削波风险。"""
    limit = 32768.0 * (10 ** (max_dbfs / 20.0))
    np = _np()
    if np is not None:
        arr = _to_float(samples)
        peak = float(np.abs(arr).max()) if arr.size else 0.0
        if peak <= limit or peak == 0:
            return samples
        gain = limit / peak
        return _from_float(arr * gain, np)
    peak = max((abs(int(s)) for s in samples), default=0)
    if peak <= limit or peak == 0:
        return samples
    gain = limit / peak
    return array.array("h", (int(s * gain) for s in samples))


def find_cut_point(
    frames: List[float], start_frame: int, min_frames: int, target_frames: int, frame_len: int
) -> Tuple[int, int]:
    """在 [start+min, target] 内找第一个静音帧作为切点；找不到就硬切在 target。"""
    lower = start_frame + min_frames
    upper = min(len(frames), target_frames)
    for i in range(lower, upper):
        if frames[i] < SILENCE_LINEAR:
            return i, i * frame_len
    idx = min(upper, len(frames))
    return idx, idx * frame_len
