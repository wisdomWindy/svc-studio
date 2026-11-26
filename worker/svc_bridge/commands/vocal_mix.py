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


ROFORMER_MODEL = "model_bs_roformer_ep_368_sdr_12.9628"


def _load_roformer_config(path: Path):
    """读 UVR 的 roformer yaml。

    配置里带着 `!!python/tuple` 这类非安全标签，`yaml.safe_load` 会拒绝，所以先把
    标签去掉再按普通列表读；随后转成 SimpleNamespace，供推理核按属性访问。
    """
    import types

    import yaml

    text = path.read_text(encoding="utf-8").replace("!!python/tuple", "")
    raw = yaml.safe_load(text)

    def to_ns(obj):
        if isinstance(obj, dict):
            return types.SimpleNamespace(**{k: to_ns(v) for k, v in obj.items()})
        if isinstance(obj, list):
            return [to_ns(v) for v in obj]
        return obj

    return raw, to_ns(raw)


def _build_roformer(model_name: str, model_kwargs: dict):
    """构建 RoFormer 网络。

    优先用 UVR 兼容实现（`audio_separator` 里的 `uvr_lib_v5.roformer`）——它的键名与
    UVR 发布的权重一致；pip 上 `BS-RoFormer` 新版改了结构（多了 `branch`/`static_alpha`），
    拿它加载 UVR 权重会整片键名对不上，所以只作兜底。
    """
    import importlib
    import inspect
    import typing

    mel = "mel_roformer" in model_name.lower() or "melband" in model_name.lower()
    mod_cls = "MelBandRoformer" if mel else "BSRoformer"
    module_tail = "mel_band_roformer" if mel else "bs_roformer"
    candidates = [
        # 仓库自带（vendor/so-vits-svc/bs_roformer_module）——发布版走这条
        (f"bs_roformer_module.roformer.{module_tail}", mod_cls),
        # 装了 audio-separator 时也可以直接用它的实现
        (f"audio_separator.separator.uvr_lib_v5.roformer.{module_tail}", mod_cls),
        # pip 的 BS-RoFormer（结构较新，UVR 权重通常对不上，仅兜底）
        ("bs_roformer", mod_cls),
    ]
    cls = None
    errors = []
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
            print(f"[separate] RoFormer 实现：{module_name}")
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{module_name}: {exc}")
    if cls is None:
        raise RuntimeError("找不到可用的 RoFormer 实现（" + "; ".join(errors) + "）")

    params = list(inspect.signature(cls.__init__).parameters.values())
    if any(p.kind == p.VAR_KEYWORD for p in params if p.name != "self"):
        kwargs = dict(model_kwargs)
    else:
        accepted = {p.name for p in params if p.name != "self"}
        dropped = sorted(set(model_kwargs) - accepted)
        if dropped:
            print(f"[separate] RoFormer 配置里这些键被忽略：{dropped}")
        kwargs = {k: v for k, v in model_kwargs.items() if k in accepted}

    # yaml 里 `!!python/tuple` 被剥掉后是 list，而实现用 beartype 校验 tuple[...] 会拒绝
    try:
        hints = typing.get_type_hints(cls.__init__)
    except Exception:  # noqa: BLE001
        hints = {}
    for key, val in list(kwargs.items()):
        if isinstance(val, list) and typing.get_origin(hints.get(key)) is tuple:
            kwargs[key] = tuple(val)
    return cls(**kwargs)


def separate_song_roformer(
    src: Path, dest_dir: Path, device: str = "", model_name: str = ROFORMER_MODEL
) -> Optional[Dict[str, Any]]:
    """BS-RoFormer 分离（MUSDB 人声 SDR 12.96，明显优于 MDX23C 的 10.4）。

    权重放 `<安装目录>/resources/separator/`，配置用 upstream 里同名 yaml；
    权重、配置、`bs_roformer` 包缺任意一个就返回 None，由调用方回退。
    """
    import os
    import sys

    model_file = _separator_asset(model_name + ".ckpt")
    up = os.environ.get("SVCSTUDIO_UPSTREAM_DIR", "")
    if model_file is None or not up:
        return None
    cfg_path = Path(up) / "mdx_model_data" / "mdx_c_configs" / (model_name + ".yaml")
    if not cfg_path.is_file():
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    vocals = dest_dir / (src.stem + ".vocals.wav")
    accomp = dest_dir / (src.stem + ".accompaniment.wav")
    if vocals.is_file() and accomp.is_file():
        return {
            "ok": True,
            "vocals": vocals,
            "accompaniment": accomp,
            "cached": True,
            "engine": f"roformer:{model_name}",
        }

    if up not in sys.path:
        sys.path.insert(0, up)
    import bs_roformer_inference as bsr  # noqa: PLC0415 - 需要 upstream 在 sys.path 上
    import soundfile as sf
    import torch

    raw_cfg, cfg = _load_roformer_config(cfg_path)
    dev = torch.device(f"cuda" if torch.cuda.is_available() else "cpu")
    if device in ("cpu", "cuda"):
        dev = torch.device(device)

    model = _build_roformer(model_name, raw_cfg["model"])
    state = torch.load(str(model_file), map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.to(dev).eval()

    mix, _sr = bsr.load_audio_stereo_at_sr(str(src), TARGET_SR)
    overlap = getattr(getattr(cfg, "inference", None), "num_overlap", 4)
    sources = bsr.demix_roformer(model, mix, dev, cfg, overlap=overlap)

    # 目标是人声：取第一个 stem；伴奏用「原曲 - 人声」保持互补，和 MDX 路径一致
    stem = next(iter(sources.values())) if sources else None
    if stem is None:
        return None
    vocal = np.asarray(stem).squeeze()
    while vocal.ndim > 2:
        vocal = vocal[0]
    if vocal.ndim == 2 and vocal.shape[0] <= vocal.shape[1]:
        vocal = vocal.T
    n = min(vocal.shape[0], mix.shape[1])
    vocal = vocal[:n]
    acc = np.asarray(mix)[:, :n].T - vocal

    sf.write(str(vocals), vocal, TARGET_SR, subtype="FLOAT")
    sf.write(str(accomp), acc, TARGET_SR, subtype="FLOAT")

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return {
        "ok": True,
        "vocals": vocals,
        "accompaniment": accomp,
        "cached": False,
        "engine": f"roformer:{model_name}",
    }


def separate_song(src: Path, dest_dir: Path, device: str = "", model: str = "htdemucs") -> Dict[str, Any]:
    """把一首歌拆成 vocals / accompaniment（44.1k wav）。

    引擎顺序可用 `SVCSTUDIO_SEPARATOR` 指定：auto（默认）/ roformer / mdx23c / demucs。
    auto = RoFormer（有资源时）→ MDX23C → Demucs。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    vocals = dest_dir / (src.stem + ".vocals.wav")
    accomp = dest_dir / (src.stem + ".accompaniment.wav")
    if vocals.is_file() and accomp.is_file():
        return {"ok": True, "vocals": vocals, "accompaniment": accomp, "cached": True}

    # 引擎顺序：RoFormer（有资源时）→ MDX23C → Demucs，可用 SVCSTUDIO_SEPARATOR 指定
    import os as _os

    choice = _os.environ.get("SVCSTUDIO_SEPARATOR", "auto").strip().lower()
    if choice in ("auto", "roformer", "bs-roformer", "bs_roformer", "mbr"):
        try:
            rf = separate_song_roformer(src, dest_dir, device)
            if rf is not None:
                return rf
            if choice != "auto":
                print("[separate] RoFormer 资源不全（权重 / 配置 / bs_roformer 包），继续回退")
        except Exception as exc:  # noqa: BLE001
            print(f"[separate] RoFormer 失败，回退：{exc}")

    if choice != "demucs":
        try:
            mdx_result = separate_song_mdx(src, dest_dir, device)
            if mdx_result is not None:
                return mdx_result
            # 返回 None 表示权重/配置没装上，不会抛异常，所以这里必须显式提示
            print(
                "[separate] 找不到 MDX23C 权重或 mdx_model_data 配置，改用 Demucs；"
                "分离质量会明显下降（UVR5 模型优先级更高）"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[separate] MDX23C 失败，回退 Demucs：{exc}")

    from demucs.api import Separator
    from demucs.audio import save_audio

    # shifts>1 与更大的 overlap 能显著减少分离伪影（代价是更慢），可用环境变量调整
    shifts = int(_os.environ.get("SVCSTUDIO_DEMUCS_SHIFTS", "2"))
    overlap = float(_os.environ.get("SVCSTUDIO_DEMUCS_OVERLAP", "0.75"))
    model = _os.environ.get("SVCSTUDIO_DEMUCS_MODEL", model)
    separator = Separator(
        model=model,
        device=_pick_device(device),
        shifts=max(1, shifts),
        overlap=min(max(overlap, 0.1), 0.9),
        progress=False,
    )
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
    return {
        "ok": True,
        "vocals": vocals,
        "accompaniment": accomp,
        "cached": False,
        "engine": f"demucs:{model}",
    }



def _mdx_overlap() -> int:
    """MDX 的重叠倍数：越大边界伪影越少、耗时线性上升。默认 8，可用环境变量调。"""
    import os

    try:
        return max(1, int(os.environ.get("SVCSTUDIO_MDX_OVERLAP", "8")))
    except ValueError:
        return 8


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

    result = mdx.mdx_c_separate(model, config, mix, dev, overlap=_mdx_overlap(), batch_size=1)
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
