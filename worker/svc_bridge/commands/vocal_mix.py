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

    # 优先 UVR5 MDX23C；模型缺失时回退 Demucs
    import os as _os

    if _os.environ.get("SVCSTUDIO_SEPARATOR", "mdx23c") != "demucs":
        try:
            mdx_result = separate_song_mdx(src, dest_dir, device)
            if mdx_result is not None:
                return mdx_result
        except Exception as exc:  # noqa: BLE001
            print(f"[separate] MDX23C 失败，回退 Demucs：{exc}")

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



def _separator_asset(name: str) -> Optional[Path]:
    """分离模型与配置放在 <安装目录>\\resources\\separator（由 Rust 传入环境变量）。"""
    import os

    for key in ("SVCSTUDIO_SEPARATOR_DIR", "SVCSTUDIO_UPSTREAM_DIR"):
        root = os.environ.get(key, "")
        if not root:
            continue
        candidate = Path(root) / name
        if candidate.exists():
            return candidate
    return None


def separate_song_mdx(src: Path, dest_dir: Path, device: str = "") -> Optional[Dict[str, Any]]:
    """用 UVR5 MDX23C 分离人声（比 Demucs 干净、高频伪影少）。

    模型或配置缺失时返回 None，调用方回退 Demucs。
    """
    model_path = _separator_asset("MDX23C-8KFFT-InstVoc_HQ.ckpt")
    data_dir = _separator_asset("mdx_model_data")
    if model_path is None or data_dir is None:
        return None

    import importlib.util
    import os

    up = os.environ.get("SVCSTUDIO_UPSTREAM_DIR", "")
    if not up:
        return None
    mdx_py = Path(up) / "mdx_inference.py"
    dec_py = Path(up) / "audio_decode.py"
    if not mdx_py.is_file() or not dec_py.is_file():
        return None

    def _load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    mdx = _load("mdx_inference", mdx_py)
    dec = _load("audio_decode", dec_py)

    dest_dir.mkdir(parents=True, exist_ok=True)
    vocals = dest_dir / (src.stem + ".vocals.wav")
    accomp = dest_dir / (src.stem + ".accompaniment.wav")
    if vocals.is_file() and accomp.is_file():
        return {"ok": True, "vocals": vocals, "accompaniment": accomp, "cached": True, "engine": "mdx23c"}

    dev = _pick_device(device)
    mix, sr = dec.load_audio_stereo_at_sr(str(src), TARGET_SR)
    config_path = Path(data_dir) / "mdx_c_configs" / "model_2_stem_full_band_8k.yaml"
    if not config_path.is_file():
        return None

    import soundfile as sf
    import torch

    config = mdx.load_mdx_c_config(str(config_path))
    model = mdx.TFC_TDF_net(config, dev)
    state = torch.load(str(model_path), map_location="cpu", weights_only=False)
    model.load_state_dict(state)
    model.to(dev).eval()

    result = mdx.mdx_c_separate(model, config, mix, dev, overlap=8, batch_size=1)
    if not isinstance(result, dict):
        return None

    written = {}
    vocal_audio = None
    for stem_name, audio_arr in result.items():
        arr = np.asarray(audio_arr).squeeze()
        while arr.ndim > 2:
            arr = arr[0]
        if arr.ndim == 2 and arr.shape[0] <= arr.shape[1]:
            arr = arr.T
        key = stem_name.lower()
        target = vocals if "vocal" in key else accomp
        sf.write(str(target), arr, TARGET_SR, subtype="FLOAT")
        written[key] = target
        if target is vocals:
            vocal_audio = arr

    if vocals not in written.values():
        return None
    if not accomp.is_file() and vocal_audio is not None:
        # 单 stem 模型只输出人声：伴奏 = 原曲 - 人声
        vmat = vocal_audio if vocal_audio.ndim == 1 else vocal_audio.T  # (C, T)
        acc = mix[:, : vmat.shape[1]] - vmat
        sf.write(str(accomp), acc.T, TARGET_SR, subtype="FLOAT")
    return {"ok": True, "vocals": vocals, "accompaniment": accomp, "cached": False, "engine": "mdx23c"}

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
