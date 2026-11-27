"""DNSMOS P.835 / P.808：无参考感知质量评分（只报告，不参与删除）。

模型来自 Microsoft DNS-Challenge（MIT），放在 `resources/pretrain/dnsmos/`：
- sig_bak_ovr.onnx：输入 9.01s@16k 波形，输出 (SIG, BAK, OVRL) 原始分
- model_v8.onnx：输入 120×900 mel，输出 P.808 MOS

实现要点与官方 `dnsmos_local.py` 一致：9.01s 窗、1s 跳、逐窗打分后平均，
P.835 三条分数再过各自的多项式校准。**域外提示**：模型训练在语音上，
对歌唱只适合看 BAK（背景干扰），不适合当"唱得好不好"的判据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import array

from .. import audio

INPUT_LENGTH = 9.01
SR = 16000
MODEL_PRIMARY = "sig_bak_ovr.onnx"
MODEL_P808 = "model_v8.onnx"

# 官方 dnsmos_local.py 的非个性化校准系数
POLY_OVR = (-0.06766283, 1.11546468, 0.04602535)
POLY_SIG = (-0.08397278, 1.22083953, 0.00524390)
POLY_BAK = (-0.13166888, 1.60915514, -0.39604546)


def model_dir() -> Optional[Path]:
    import os

    env = os.environ.get("SVCSTUDIO_DNSMOS_DIR")
    if env and (Path(env) / MODEL_PRIMARY).is_file():
        return Path(env)
    here = Path(__file__).resolve()
    for up in list(here.parents)[:7]:
        for rel in ("resources/pretrain/dnsmos",):
            candidate = up / rel
            if (candidate / MODEL_PRIMARY).is_file() and (candidate / MODEL_P808).is_file():
                return candidate
    return None


def available() -> bool:
    if model_dir() is None:
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _sessions():
    import onnxruntime as ort

    d = model_dir()
    assert d is not None
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    primary = ort.InferenceSession(
        str(d / MODEL_PRIMARY), sess_options=opts, providers=["CPUExecutionProvider"]
    )
    p808 = ort.InferenceSession(
        str(d / MODEL_P808), sess_options=opts, providers=["CPUExecutionProvider"]
    )
    return primary, p808


def _mel(segment):
    np = audio._np()  # noqa: SLF001
    import librosa

    mel = librosa.feature.melspectrogram(
        y=segment, sr=SR, n_fft=321, hop_length=160, n_mels=120, window="hann"
    )
    mel = (librosa.power_to_db(mel, ref=np.max) + 40) / 40
    return mel.T.astype("float32")


def _score_array(samples: array.array, sample_rate: int) -> Optional[Dict[str, float]]:
    """对一段音频打分；不足 9.01s 时循环填充（与官方实现一致）。"""
    if not available():
        return None
    np = audio._np()  # noqa: SLF001
    import librosa

    x = np.frombuffer(samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
    if sample_rate != SR:
        x = librosa.resample(x, orig_sr=sample_rate, target_sr=SR)
    need = int(INPUT_LENGTH * SR)
    if len(x) < need:
        while len(x) < need:
            x = np.append(x, x)
    hops = int(len(x) // SR - INPUT_LENGTH) + 1
    if hops < 1:
        hops = 1

    primary, p808 = _sessions()
    sig, bak, ovr, p808s = [], [], [], []
    for idx in range(hops):
        seg = x[int(idx * SR) : int((idx + INPUT_LENGTH) * SR)]
        if len(seg) < need:
            continue
        feats = seg.astype("float32")[np.newaxis, :]
        raw = primary.run(None, {"input_1": feats})[0][0]
        sig.append(float(np.polyval(POLY_SIG, raw[0])))
        bak.append(float(np.polyval(POLY_BAK, raw[1])))
        ovr.append(float(np.polyval(POLY_OVR, raw[2])))
        mel = _mel(seg[:-160])[np.newaxis, :, :]
        p808s.append(float(p808.run(None, {"input_1": mel})[0][0][0]))

    if not ovr:
        return None
    return {
        "sig": round(float(sum(sig) / len(sig)), 3),
        "bak": round(float(sum(bak) / len(bak)), 3),
        "ovrl": round(float(sum(ovr) / len(ovr)), 3),
        "p808": round(float(sum(p808s) / len(p808s)), 3),
        "segments": len(ovr),
    }


def score_file(path: Path) -> Optional[Dict[str, Any]]:
    wav = audio.read_wav(Path(path))
    samples = audio.to_mono_if_needed(wav)
    out = _score_array(samples, wav.sample_rate)
    if out is None:
        return None
    out["relPath"] = Path(path).name
    return out


def scan(root: str, limit: int = 0) -> Dict[str, Any]:
    """给目录下所有 wav 打 DNSMOS 分（只报告）。"""
    base = Path(root)
    entries: List[Dict[str, Any]] = []
    failed: List[str] = []
    if not available():
        return {
            "schema": "svcstudio.dataset.mos/1",
            "available": False,
            "note": "缺少 DNSMOS 模型或 onnxruntime（resources/pretrain/dnsmos）",
            "files": 0,
            "entries": [],
            "failed": [],
        }
    if not base.is_dir():
        return {
            "schema": "svcstudio.dataset.mos/1",
            "available": True,
            "files": 0,
            "entries": [],
            "failed": [],
            "summary": {},
        }

    files = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        files.extend(sorted(spk.rglob("*.wav")))
    files.extend(sorted(base.glob("*.wav")))

    for path in files:
        if limit and len(entries) >= limit:
            break
        try:
            out = score_file(path)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{path.name}: {exc}")
            continue
        if out:
            entries.append(out)

    def median(key: str) -> Optional[float]:
        vals = sorted(e[key] for e in entries if isinstance(e.get(key), (int, float)))
        return round(vals[len(vals) // 2], 3) if vals else None

    return {
        "schema": "svcstudio.dataset.mos/1",
        "available": True,
        "files": len(entries),
        "summary": {
            "medianOvrl": median("ovrl"),
            "medianSig": median("sig"),
            "medianBak": median("bak"),
            "medianP808": median("p808"),
            "worstOvrl": min((e["ovrl"] for e in entries), default=None),
        },
        "entries": entries[:500],
        "failed": failed[:50],
    }
