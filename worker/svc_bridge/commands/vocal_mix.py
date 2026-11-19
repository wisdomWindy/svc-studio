"""推理前的人声分离与混音。

so-vits-svc 会把整个输入当成人声来转换：直接喂整首歌，伴奏也会被"唱"一遍。
这里先把输入拆成人声/伴奏，只转换人声，再把转换后的人声与**原伴奏**混合，
这样伴奏、混音比例保持不变。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from .. import audio

TARGET_SR = 44100


def _pick_device(requested: str = "") -> str:
    try:
        import torch

        if requested in ("cpu", "cuda"):
            return requested
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def separate_song(src: Path, dest_dir: Path, device: str = "", model: str = "htdemucs") -> Dict[str, Any]:
    """把一首歌拆成 vocals / accompaniment（44.1k 单声道 wav）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    vocals = dest_dir / (src.stem + ".vocals.wav")
    accomp = dest_dir / (src.stem + ".accompaniment.wav")
    if vocals.is_file() and accomp.is_file():
        return {"ok": True, "vocals": vocals, "accompaniment": accomp, "cached": True}

    from demucs.api import Separator
    from demucs.audio import save_audio

    separator = Separator(model=model, device=_pick_device(device), progress=False)
    _origin, stems = separator.separate_audio_file(str(src))
    save_audio(stems["vocals"], str(vocals), separator.samplerate)

    # 伴奏 = 其余所有声部的和
    other: Optional[np.ndarray] = None
    for name, tensor in stems.items():
        if name == "vocals":
            continue
        arr = tensor.detach().cpu().numpy()
        other = arr if other is None else other + arr
    if other is None:
        return {"ok": False, "error": "分离结果没有伴奏声部"}

    from demucs.audio import save_audio as save

    import torch

    save(torch.from_numpy(other), str(accomp), separator.samplerate)
    return {"ok": True, "vocals": vocals, "accompaniment": accomp, "cached": False}


def _read(path: Path) -> tuple[np.ndarray, int]:
    """读 44.1k 单声道样本；wav 用自研解析，其它格式（flac 等）走 soundfile。"""
    try:
        wav = audio.read_wav(path)
        data = np.asarray(wav.samples, dtype=np.int16).astype(np.float32) / 32768.0
        sr = wav.sample_rate
    except Exception:
        import soundfile as sf

        raw, sr = sf.read(str(path), dtype="float32", always_2d=False)
        data = raw.mean(axis=1) if getattr(raw, "ndim", 1) > 1 else raw
        return np.asarray(data, dtype=np.float32), int(sr)
    if data.ndim > 1:
        data = data.mean(axis=0)
    return data, sr


def mix_tracks(vocal: Path, accompaniment: Path, out_path: Path, vocal_gain: float = 1.0) -> Dict[str, Any]:
    """人声 + 原伴奏混音；以伴奏时长为准对齐，峰值超限时整体衰减。"""
    v, v_sr = _read(vocal)
    a, a_sr = _read(accompaniment)
    if v_sr != TARGET_SR:
        v = _resample(v, v_sr, TARGET_SR)
    if a_sr != TARGET_SR:
        a = _resample(a, a_sr, TARGET_SR)

    n = max(len(v), len(a))
    v = np.pad(v, (0, n - len(v)))
    a = np.pad(a, (0, n - len(a)))
    mixed = v * float(vocal_gain) + a

    peak = float(np.max(np.abs(mixed))) if n else 0.0
    gain = 1.0
    if peak > 0.98:
        gain = 0.98 / peak
        mixed = mixed * gain

    pcm = np.clip(mixed * 32767.0, -32768, 32767).astype(np.int16)
    audio.write_wav(out_path, pcm, TARGET_SR, 1)
    return {
        "output": str(out_path),
        "seconds": round(n / TARGET_SR, 3),
        "appliedGain": round(gain, 4),
        "peak": round(peak, 4),
    }


def _resample(data: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return data
    try:
        import librosa

        return librosa.resample(data, orig_sr=sr, target_sr=target)
    except Exception:
        # 最邻近兜底，保证长度与采样率正确
        idx = np.linspace(0, len(data) - 1, int(len(data) * target / sr))
        return data[idx.astype(np.int64)]


def cleanup(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
