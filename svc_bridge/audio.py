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
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    rms = math.sqrt(acc / len(samples)) / 32768.0
    return 20.0 * math.log10(max(rms, 1e-12))


def peak_dbfs(samples: array.array) -> float:
    if not samples:
        return -120.0
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


def to_mono_if_needed(wav: Wav) -> array.array:
    return wav.mono() if wav.channels > 1 else wav.samples


def protect_peak(samples: array.array, max_dbfs: float = -1.0) -> array.array:
    """把峰值压到 max_dbfs，替代上游"归一化到 0dBFS"的削波风险。"""
    limit = 32768.0 * (10 ** (max_dbfs / 20.0))
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
