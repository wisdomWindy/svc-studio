"""refine 阶段：按绝对阈值精修切片（时长/噪声底/带宽/近重复）+ 响度归一化。"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands.dataset_refine import refine_tree

np = pytest.importorskip("numpy")


def _tone(seconds: float, amp: float, hf: bool = True) -> array.array:
    """生成测试音；hf=False 时只有低频（模拟 15kHz 截断素材）。"""
    sr = 44100
    out = array.array("h")
    n = int(seconds * sr)
    for i in range(n):
        t = i / sr
        v = 0.6 * math.sin(2 * math.pi * 220 * t) + 0.3 * math.sin(2 * math.pi * 660 * t)
        if hf:
            v += 0.1 * math.sin(2 * math.pi * 12000 * t)
        out.append(int(max(-1.0, min(1.0, amp * v)) * 32767))
    return out


def _put(root: Path, name: str, samples, sr: int = 44100) -> Path:
    path = root / "spk" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.write_wav(path, samples, sr, 1)
    return path


def test_refine_drops_too_short_and_keeps_normal(work: Path) -> None:
    root = work / "dataset_raw"
    _put(root, "ok.wav", _tone(8, 0.5))
    _put(root, "short.wav", _tone(2, 0.5))

    result = refine_tree(str(root), normalize=False, dedup_similarity=0, min_snr_db=0)

    assert (root / "spk" / "ok.wav").exists()
    assert not (root / "spk" / "short.wav").exists(), "2s 的残缺片段必须删除"
    reasons = [r["reason"] for r in result["rejected"]]
    assert any("时长" in r for r in reasons)


def test_refine_drops_bandlimited_when_floor_set(work: Path) -> None:
    root = work / "dataset_raw"
    _put(root, "bright.wav", _tone(8, 0.5, hf=True))
    _put(root, "dull.wav", _tone(8, 0.5, hf=False))

    result = refine_tree(str(root), normalize=False, dedup_similarity=0, min_snr_db=0, min_rolloff_hz=9000)

    assert (root / "spk" / "bright.wav").exists()
    assert not (root / "spk" / "dull.wav").exists(), "带宽低于阈值的切片应被删除"
    assert any("带宽" in r["reason"] for r in result["rejected"])


def test_refine_dedups_near_duplicates(work: Path) -> None:
    root = work / "dataset_raw"
    base = _tone(8, 0.5)
    _put(root, "a.wav", base)
    _put(root, "b.wav", base)  # 完全相同 → 必删其一
    _put(root, "c.wav", _tone(8, 0.2, hf=True))  # 能量不同但音色相近，仍可能被判重

    result = refine_tree(str(root), normalize=False, dedup_similarity=0.995, min_snr_db=0)

    assert result["duplicates"] >= 1
    assert result["keptFiles"] == result["inputFiles"] - result["rejectedFiles"]


def test_refine_normalizes_loudness_without_clipping(work: Path) -> None:
    root = work / "dataset_raw"
    quiet = _tone(8, 0.08)
    loud = _tone(8, 0.95)
    _put(root, "quiet.wav", quiet)
    _put(root, "loud.wav", loud)

    result = refine_tree(str(root), normalize=True, dedup_similarity=0, min_snr_db=0, target_rms_dbfs=-20.0)

    assert result["normalized"] == 2
    rms = []
    for name in ("quiet.wav", "loud.wav"):
        wav = audio.read_wav(root / "spk" / name)
        x = np.frombuffer(wav.samples.tobytes(), dtype="<i2").astype(np.float64) / 32768.0
        rms.append(20 * math.log10(float(np.sqrt((x**2).mean()))))
        assert np.abs(x).max() <= 0.9, "归一化后峰值必须留余量"
    assert abs(rms[0] - rms[1]) < 1.5, f"两段响度应被拉齐，实际 {rms}"


def test_refine_reports_kept_stats(work: Path) -> None:
    root = work / "dataset_raw"
    _put(root, "a.wav", _tone(8, 0.4))
    result = refine_tree(str(root), dedup_similarity=0, min_snr_db=0)
    stats = result["keptStats"]
    assert result["keptFiles"] == 1
    assert stats["secondsTotal"] == pytest.approx(8.0, abs=0.1)
    assert stats["rolloffHzMedian"] and stats["rolloffHzMedian"] > 8000
