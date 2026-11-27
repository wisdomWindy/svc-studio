"""源音频体检：分离前挑出「客观不可用」的文件，避免白跑一次 GPU 分离。

注意：这里**不评判演唱内容**。阈值只覆盖有明显客观缺陷的文件（解码失败、削波、
长静音、时长异常）。混音上的 SNR 代理没有意义（安静段是伴奏在响），所以不用它筛源文件。
"""

from __future__ import annotations

import array
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .. import audio

# 阈值取自实测：正常流行歌的削波为 0、静音中位数 0.5%、时长 15s–4.5min
MAX_CLIP_RATIO = 0.001  # 削波样本占比 ≥ 0.1%
MAX_SILENCE_RATIO = 0.60  # 全文件静音占比 ≥ 60%
MIN_SECONDS = 5.0
MAX_SECONDS = 600.0

# 分离后：干声整体电平低于 -45 dBFS 视为「几乎没分离出人声」
MIN_VOCAL_RMS_DBFS = -45.0


def _ffmpeg() -> Optional[str]:
    env = os.environ.get("SVCSTUDIO_FFMPEG")
    if env and Path(env).is_file():
        return env
    return shutil.which("ffmpeg")


def decode_mono(path: Path) -> Optional[audio.Wav]:
    """把任意支持格式解成内存里的 16-bit PCM；失败返回 None。"""
    try:
        return audio.read_wav(path)
    except Exception:  # noqa: BLE001 - mp3/m4a/float wav 都会走到 ffmpeg
        pass
    ff = _ffmpeg()
    if not ff:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "decoded.wav"
        proc = subprocess.run(
            [
                ff,
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(out),
            ],
            capture_output=True,
        )
        if proc.returncode != 0 or not out.is_file():
            return None
        try:
            return audio.read_wav(out)
        except Exception:  # noqa: BLE001
            return None


def _rolloff_hz(samples: array.array, sample_rate: int, share: float = 0.99) -> Optional[float]:
    """99% 能量滚降频率；没有 numpy 就跳过（只做提示，不参与拦截）。"""
    np = audio._np()  # noqa: SLF001 - 同包内部约定
    if np is None:
        return None
    arr = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    limit = min(len(arr), sample_rate * 30)
    if limit < 2:
        return None
    spec = np.abs(np.fft.rfft(arr[:limit]))
    freqs = np.fft.rfftfreq(limit, 1.0 / sample_rate)
    total = float(spec.sum())
    if total <= 0:
        return None
    cumulative = np.cumsum(spec)
    idx = int(np.searchsorted(cumulative, total * share))
    return float(freqs[min(idx, len(freqs) - 1)])


def analyze(path: Path) -> Dict[str, Any]:
    """返回该文件的体检指标；不可读时 readable=False。"""
    wav = decode_mono(path)
    if wav is None:
        return {"readable": False, "error": "解码失败"}
    samples = audio.to_mono_if_needed(wav)
    return {
        "readable": True,
        "seconds": round(wav.seconds, 2),
        "clipRatio": round(audio.clipping_ratio(samples), 6),
        "silenceRatio": round(audio.silence_ratio(samples, wav.sample_rate), 4),
        "peakDbfs": round(audio.peak_dbfs(samples), 2),
        "rmsDbfs": round(audio.rms_dbfs(samples), 2),
        "rolloffHz": (
            round(_rolloff_hz(samples, wav.sample_rate), 0)
            if _rolloff_hz(samples, wav.sample_rate) is not None
            else None
        ),
    }


def gate_reason(metrics: Dict[str, Any]) -> Optional[str]:
    """返回拦截原因；可处理返回 None。只拦客观不可用，不评判演唱。"""
    if not metrics.get("readable"):
        return "解码失败"
    seconds = float(metrics.get("seconds") or 0.0)
    if seconds < MIN_SECONDS:
        return f"时长过短 {seconds:.1f}s < {MIN_SECONDS:.0f}s"
    if seconds > MAX_SECONDS:
        return f"时长过长 {seconds:.0f}s > {MAX_SECONDS:.0f}s"
    if float(metrics.get("clipRatio") or 0.0) >= MAX_CLIP_RATIO:
        return f"削波 {float(metrics['clipRatio']) * 100:.2f}% ≥ {MAX_CLIP_RATIO * 100:.1f}%"
    if float(metrics.get("silenceRatio") or 0.0) >= MAX_SILENCE_RATIO:
        return f"静音 {float(metrics['silenceRatio']) * 100:.0f}% ≥ {MAX_SILENCE_RATIO * 100:.0f}%"
    return None


def vocal_too_quiet(vocals_path: Path) -> Optional[float]:
    """分离后的干声是否几乎为空；返回实测 RMS(dBFS)，可接受时返回 None。"""
    try:
        wav = audio.read_wav(vocals_path)
    except Exception:  # noqa: BLE001
        return None
    samples = audio.to_mono_if_needed(wav)
    rms = audio.rms_dbfs(samples)
    return rms if rms < MIN_VOCAL_RMS_DBFS else None

