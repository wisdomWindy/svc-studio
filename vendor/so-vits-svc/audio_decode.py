"""
通用音频解码（分离管线共用）
优先 FFmpeg，覆盖 MP3 / M4A / FLAC / WAV / AAC / OPUS / 视频容器中的音轨等；
避免 Windows 上 librosa→audioread 对部分格式卡顿或假死。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def _run_subprocess_hidden(cmd: list) -> subprocess.CompletedProcess:
    """Windows 下避免 ffmpeg 弹控制台或卡在桌面会话；其它平台保持默认。"""
    kw = dict(
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


def ffmpeg_binary():
    """整合包 ffmpeg/bin 或系统 PATH。"""
    w = shutil.which("ffmpeg")
    if w:
        return w
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(base, "ffmpeg", "bin", name)
        if os.path.isfile(p):
            return p
    return None


def _emit(log, msg: str) -> None:
    if log is not None and hasattr(log, "log"):
        log.log(msg)
    else:
        print(msg)


def _frames_to_stereo_ch_first_f32(data: np.ndarray, sr_src: int, target_sr: int) -> np.ndarray:
    """soundfile 典型输出 (frames, ch) → [2, T] float32，并按需重采样到 target_sr。"""
    if data.ndim == 1:
        data = np.column_stack([data, data])
    elif data.shape[1] == 1:
        c = data[:, 0]
        data = np.column_stack([c, c])
    elif data.shape[1] > 2:
        data = data[:, :2]
    if sr_src == target_sr:
        return data.T.astype(np.float32)
    c0 = librosa.resample(data[:, 0].astype(np.float32), orig_sr=sr_src, target_sr=target_sr)
    c1 = librosa.resample(data[:, 1].astype(np.float32), orig_sr=sr_src, target_sr=target_sr)
    n = min(c0.shape[-1], c1.shape[-1])
    return np.stack([c0[:n], c1[:n]], axis=0)


def load_audio_stereo_at_sr(audio_path, target_sr, log=None):
    """
    加载为立体声 float32，形状 [2, T]，采样率 target_sr。

    Parameters
    ----------
    audio_path : str
    target_sr : int | float
    log : optional
        具备 .log(str) 的对象（如 WebUI 日志收集器）；否则 print。

    Returns
    -------
    mix : np.ndarray
        shape [2, T], dtype float32
    sr : int
        等于 int(round(target_sr))
    """
    tsr = int(round(float(target_sr)))
    path = os.path.normpath(os.path.abspath(audio_path))
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    ext = Path(path).suffix.lower()

    ff = ffmpeg_binary()
    if ff is not None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            _emit(log, f"[音频] FFmpeg 解码 → {tsr} Hz 立体声（通用格式：MP3/M4A/FLAC/WAV 等）…")
            if log is None:
                print(f"[audio_decode] FFmpeg start: {ff}", flush=True)
            cmd = [
                ff,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads",
                "0",
                "-i",
                path,
                "-vn",
                "-ac",
                "2",
                "-ar",
                str(tsr),
                tmp_path,
            ]
            proc = _run_subprocess_hidden(cmd)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(err or f"ffmpeg 退出码 {proc.returncode}")
            data, sr_src = sf.read(tmp_path, always_2d=True, dtype="float32")
            mix = _frames_to_stereo_ch_first_f32(data, int(sr_src), tsr)
            if log is None:
                print("[audio_decode] FFmpeg done", flush=True)
            return mix, tsr
        except Exception as e:
            _emit(log, f"[音频] FFmpeg 解码失败，尝试后备读取: {e}")
            if log is None:
                print(f"[audio_decode] FFmpeg error: {e}", flush=True)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if ext in (".wav", ".flac", ".ogg", ".aiff", ".aif", ".wv"):
        try:
            data, sr_src = sf.read(path, always_2d=True, dtype="float32")
            mix = _frames_to_stereo_ch_first_f32(data, int(sr_src), tsr)
            return mix, tsr
        except Exception as e:
            _emit(log, f"[音频] soundfile 读取失败: {e}")

    _emit(log, "[音频] 使用 librosa.load（若无 FFmpeg，部分格式可能较慢或失败）…")
    mix, _ = librosa.load(path, sr=tsr, mono=False)
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=0)
    elif mix.shape[0] > 2:
        mix = mix.T
    return mix.astype(np.float32), tsr
