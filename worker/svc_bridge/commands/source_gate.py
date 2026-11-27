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
# 上限只做「明显不是单个音频」的兜底：合并好的长语料（几十分钟）是合法输入，
# 后面的 slice 会把它切成 5–15s。之前设 600s 会把 merged_*.wav 这类文件误杀。
MAX_SECONDS = 4 * 3600.0

# 分离后：干声整体电平低于 -45 dBFS 视为「几乎没分离出人声」
MIN_VOCAL_RMS_DBFS = -45.0


def _ffmpeg() -> Optional[str]:
    """找解码器：环境变量 → PATH → 安装目录下的 resources/bin/ffmpeg.exe。"""
    env = os.environ.get("SVCSTUDIO_FFMPEG")
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 从 worker 目录向上找安装根的 resources/bin（app 未设置环境变量时兜底）
    here = Path(__file__).resolve()
    for up in list(here.parents)[:7]:
        for rel in ("resources/bin/ffmpeg.exe", "resources/bin/ffmpeg"):
            candidate = up / rel
            if candidate.is_file():
                return str(candidate)
    return None


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
    """[已废弃] 幅度谱 99% 滚动频率。

    保留仅为兼容旧调用；它测的不是带宽（真实音乐上会报 3316 Hz）。
    新代码一律用 bandwidth_db()。
    """
    np = audio._np()  # noqa: SLF001
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


def bandwidth_db(
    samples: array.array,
    sample_rate: int,
    db: float = 50.0,
    smooth: int = 64,
    abs_floor_db: float = 100.0,
) -> Optional[float]:
    """带宽 = 相对参考电平 -db 的最高频率（已用已知真值信号标定）。

    算法：功率谱（Hann）→ 幅度谱滑动平均 → 参考 = max(1–4kHz 均值, 峰值×1e-6)
    → 阈值 = max(参考×10^(-db/20), 峰值×10^(-abs_floor_db/20)) → 从 50Hz 起取最高满足项。

    标定结果（误差）：白噪声 22050Hz 0.0% / 15kHz 低通 15003Hz 0.0% /
    8kHz 低通 8003Hz 0.0% / 220Hz 纯音 223Hz 1.5%。
    """
    np = audio._np()  # noqa: SLF001
    if np is None or not samples:
        return None
    arr = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    if len(arr) < sample_rate // 2:
        return None
    limit = min(len(arr), sample_rate * 60)
    seg = arr[:limit] * np.hanning(limit)
    amp = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(limit, 1.0 / sample_rate)
    if smooth > 1 and len(amp) > smooth:
        amp = np.convolve(amp, np.ones(smooth) / smooth, mode="same")
    peak = float(amp.max())
    if peak <= 0:
        return 0.0
    i1 = int(np.searchsorted(freqs, 1000))
    i2 = int(np.searchsorted(freqs, 4000))
    band = float(amp[i1:i2].mean()) if i2 > i1 else peak
    ref = max(band, peak * 1e-6)
    thresh = max(ref * 10 ** (-db / 20.0), peak * 10 ** (-abs_floor_db / 20.0))
    start = int(np.searchsorted(freqs, 50))
    mask = amp[start:] >= thresh
    if not mask.any():
        return 0.0
    return float(freqs[start + int(np.flatnonzero(mask)[-1])])


def analyze(path: Path) -> Dict[str, Any]:
    """返回该文件的体检指标；不可读时 readable=False。"""
    wav = decode_mono(path)
    if wav is None:
        return {
            "readable": False,
            # 缺解码器 = 环境问题，不能当成坏文件删除
            "noDecoder": _ffmpeg() is None and path.suffix.lower() not in {".wav", ".wave"},
            "error": "解码失败",
        }
    samples = audio.to_mono_if_needed(wav)
    # 带宽用标定过的 -50dB 口径（旧的 99% 幅度滚降会低估真实音乐带宽）
    bw = bandwidth_db(samples, wav.sample_rate)
    nyquist = wav.sample_rate / 2.0
    return {
        "readable": True,
        "seconds": round(wav.seconds, 2),
        "sampleRate": wav.sample_rate,
        "clipRatio": round(audio.clipping_ratio(samples), 6),
        "silenceRatio": round(audio.silence_ratio(samples, wav.sample_rate), 4),
        "peakDbfs": round(audio.peak_dbfs(samples), 2),
        "rmsDbfs": round(audio.rms_dbfs(samples), 2),
        "rolloffHz": round(bw, 0) if bw is not None else None,
        "nyquistHz": round(nyquist, 0),
        # 读数贴近 Nyquist 说明是「采样率限制」而不是素材缺高频，不能判偏闷
        "samplingRateLimited": bool(bw is not None and bw >= nyquist * 0.95),
    }


def gate_reason(metrics: Dict[str, Any]) -> Optional[str]:
    """返回拦截原因；可处理返回 None。只拦客观不可用，不评判演唱。

    注意：如果读不出来是**环境缺解码器**（ffmpeg 没就绪）而不是文件本身坏，
    绝不能判文件死刑——否则一个配置问题就会把整个数据集删光。这种情况返回 None，
    让后面的分离流程自己去报错。
    """
    if not metrics.get("readable"):
        if metrics.get("noDecoder"):
            return None
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

