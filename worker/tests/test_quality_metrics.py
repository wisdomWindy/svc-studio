"""分离质量指标标定：用构造的「已知分离程度」信号验证 bleed / ratio 读数。"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import quality_metrics as qm

np = pytest.importorskip("numpy")


def _sig(seconds: float = 3.0, freq: float = 220.0, amp: float = 0.3) -> array.array:
    sr = 44100
    out = array.array("h")
    for i in range(int(seconds * sr)):
        out.append(int(amp * math.sin(2 * math.pi * freq * i / sr) * 32767))
    return out


def _noise(seconds: float = 3.0, amp: float = 0.3, seed: int = 0) -> array.array:
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, int(seconds * 44100))
    x = x / np.abs(x).max() * amp
    return array.array("h", (x * 32767).astype("int16").tobytes())


def test_bleed_is_near_zero_for_independent_signals() -> None:
    """真分离：人声与伴奏无关 → bleed ≈ 0。"""
    assert qm.bleed_corr(_sig(freq=220), _noise(seed=1)) < 0.05


def test_bleed_is_near_one_for_identical_signals() -> None:
    """没分开：两个 stem 是同一段音频 → bleed ≈ 1。"""
    same = _sig(freq=220)
    assert qm.bleed_corr(same, array.array("h", same)) > 0.999


def test_bleed_is_high_for_mixed_stems() -> None:
    """分离不彻底：伴奏里也混着人声 → bleed 明显高于 0。"""
    v = _sig(freq=220)
    a = _sig(freq=220)  # 伴奏里残留同样的人声成分
    mixed = array.array("h", ((x + y) // 2 for x, y in zip(v, a)))
    assert qm.bleed_corr(v, mixed) > 0.4


def test_vocal_ratio_matches_known_gain() -> None:
    """人声比伴奏高 6 dB 时，vocalRatioDb 应读 ≈ +6 dB。"""
    v = _sig(amp=0.4)
    a = _sig(freq=330, amp=0.2)  # 幅度减半 → 能量差 4 倍 → 6.02 dB
    got = qm.ratio_db(qm.energy(v), qm.energy(a))
    assert got == pytest.approx(6.02, abs=0.3)


def test_mix_ratio_consistent_with_reconstructed_mix() -> None:
    """mixRatioDb 用能量+相关系数还原，必须与「真的把两路相加」算出的比例一致。"""
    v = _sig(amp=0.3, freq=220)
    a = _noise(amp=0.2, seed=3)
    n = min(len(v), len(a))
    mix = array.array("h", ((v[i] + a[i]) for i in range(n)))
    expected = qm.ratio_db(qm.energy(array.array("h", v[:n])), qm.energy(mix))

    corr = qm.bleed_corr(v, a)
    e_v = qm.energy(array.array("h", v[:n]))
    e_a = qm.energy(array.array("h", a[:n]))
    e_mix = e_v + e_a + 2 * corr * math.sqrt(e_v * e_a)
    got = qm.ratio_db(e_v, e_mix)

    assert got == pytest.approx(expected, abs=1.0)


def test_measure_reads_files_and_summarizes(work: Path) -> None:
    vp = work / "v.wav"
    ap = work / "a.wav"
    # 用同类型的正弦（相同波峰因数）比较能量比，避免把 crest factor 差异当成增益差
    audio.write_wav(vp, _sig(amp=0.4), 44100)
    audio.write_wav(ap, _sig(freq=330, amp=0.2), 44100)

    m = qm.measure(vp, ap)
    assert m["bleed"] is not None
    assert m["vocalRatioDb"] == pytest.approx(6.0, abs=0.5)

    summary = qm.summarize([m, m])
    assert summary["fileCount"] == 2
    assert summary["medianBleed"] == m["bleed"]


def test_measure_bleed_is_low_for_noise_accompaniment(work: Path) -> None:
    vp = work / "v.wav"
    ap = work / "n.wav"
    audio.write_wav(vp, _sig(amp=0.4), 44100)
    audio.write_wav(ap, _noise(amp=0.2, seed=5), 44100)
    m = qm.measure(vp, ap)
    assert m["bleed"] is not None and m["bleed"] < 0.1


def test_measure_without_accompaniment(work: Path) -> None:
    vp = work / "v.wav"
    audio.write_wav(vp, _sig(), 44100)
    m = qm.measure(vp, None)
    assert m["bleed"] is None and m["vocalRatioDb"] is None

def _write_float_wav(path: Path, samples, sr: int = 44100) -> Path:
    """写 32-bit float WAV（format=3）：分离出的 stem 常是这种格式。"""
    import struct

    data = np.asarray(samples, dtype="<f4").tobytes()
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, sr, sr * 4, 4, 32)
    header += b"data" + struct.pack("<I", len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + data)
    return path


def test_measure_reads_float32_stems(work: Path) -> None:
    """回归：float32 stem 曾是 unknown format: 3，必须能读。"""
    vp = work / "v_f32.wav"
    ap = work / "a_f32.wav"
    x = np.linspace(0, 1, 44100 * 3, endpoint=False)
    _write_float_wav(vp, 0.4 * np.sin(2 * np.pi * 220 * x))
    _write_float_wav(ap, 0.2 * np.sin(2 * np.pi * 330 * x))

    m = qm.measure(vp, ap)
    assert m["vocalRatioDb"] == pytest.approx(6.0, abs=0.5)
    assert m["bleed"] is not None
