"""导入前的带宽预估：素材上限 = 模型输出亮度上限。"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset_bandwidth


def _write(path: Path, seconds: float, hf_hz: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sr = 44100
    out = array.array("h")
    for i in range(int(seconds * sr)):
        t = i / sr
        v = 0.5 * math.sin(2 * math.pi * 220 * t) + 0.3 * math.sin(2 * math.pi * hf_hz * t)
        out.append(int(max(-1.0, min(1.0, v)) * 32767))
    audio.write_wav(path, out, sr, 1)
    return path


def test_empty_dataset_reports_nothing(work: Path) -> None:
    result = dataset_bandwidth.scan(str(work / "dataset_raw"))
    assert result["files"] == 0
    assert result["estimatedOutputCeilingHz"] is None
    assert result["verdict"]["label"] == "无素材"


def test_bright_material_is_good(work: Path) -> None:
    root = work / "dataset_raw"
    _write(root / "spk" / "bright.wav", 4, 19000)
    result = dataset_bandwidth.scan(str(root))
    assert result["files"] == 1
    assert result["medianRolloffHz"] >= 16000
    assert result["verdict"]["level"] == "good"
    assert result["estimatedOutputCeilingHz"] == result["medianRolloffHz"]


def test_dull_material_warns_about_expectation(work: Path) -> None:
    root = work / "dataset_raw"
    _write(root / "spk" / "dull.wav", 4, 6000)
    result = dataset_bandwidth.scan(str(root))
    assert result["medianRolloffHz"] < 14000
    assert result["verdict"]["level"] in {"dull", "very-dull"}
    assert "偏闷" in result["verdict"]["label"]
    # 预估上限不允许超过 20 kHz
    assert result["estimatedOutputCeilingHz"] <= 20000


def test_buckets_cover_all_files(work: Path) -> None:
    root = work / "dataset_raw"
    _write(root / "spk" / "a.wav", 3, 18000)
    _write(root / "spk" / "b.wav", 3, 5000)
    result = dataset_bandwidth.scan(str(root))
    assert sum(result["buckets"].values()) == result["files"] == 2
    assert result["minRolloffHz"] <= result["medianRolloffHz"] <= result["maxRolloffHz"]


def test_skips_underscore_directories(work: Path) -> None:
    root = work / "dataset_raw"
    _write(root / "spk" / "a.wav", 3, 18000)
    _write(root / "_original" / "spk" / "b.wav", 3, 5000)
    result = dataset_bandwidth.scan(str(root))
    assert result["files"] == 1, "备份目录（_original 等）不参与体检"

def _write_noise(path: Path, seconds: float, sr: int, lowpass: float | None = None) -> Path:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.1, int(seconds * sr))
    if lowpass:
        spec = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(len(x), 1 / sr)
        spec[freqs > lowpass] = 0
        x = np.fft.irfft(spec, n=len(x))
    audio.write_wav(path, array.array("h", (x * 32767).astype("int16").tobytes()), sr, 1)
    return path


def test_bandwidth_db_matches_known_ground_truth(work: Path) -> None:
    """标定：已知真值的信号，-50dB 口径误差必须 ≤5%。"""
    import numpy as np

    from svc_bridge.commands.source_gate import bandwidth_db

    cases = [(22050, None), (15000, 15000), (8000, 8000)]
    for truth, lp in cases:
        path = _write_noise(work / f"n{truth}.wav", 5, 44100, lowpass=lp)
        wav = audio.read_wav(path)
        got = bandwidth_db(wav.samples, wav.sample_rate)
        assert got is not None
        assert abs(got - truth) / truth <= 0.05, f"真值 {truth}Hz，实测 {got:.0f}Hz"


def test_24k_audio_is_sampling_rate_limited_not_dull(work: Path) -> None:
    """24kHz 文件在容器内已满带宽，不能判成「偏闷」（真实参考音曾因此被误判）。"""
    root = work / "dataset_raw"
    _write_noise(root / "spk" / "ref24k.wav", 5, 24000)
    result = dataset_bandwidth.scan(str(root))

    assert result["samplingRateLimitedFiles"] == 1
    assert result["verdict"]["level"] == "sampling-limited"
    assert "采样率" in result["verdict"]["label"] or "满带宽" in result["verdict"]["label"]
    assert result["medianRolloffHz"] >= 24000 / 2 * 0.95


def test_44k_wideband_is_good(work: Path) -> None:
    root = work / "dataset_raw"
    _write_noise(root / "spk" / "wide.wav", 5, 44100)
    result = dataset_bandwidth.scan(str(root))
    assert result["verdict"]["level"] == "good"
    assert result["samplingRateLimitedFiles"] == 1  # 44.1k 满带宽也算贴 Nyquist


def test_44k_bandlimited_is_not_sampling_limited(work: Path) -> None:
    root = work / "dataset_raw"
    _write_noise(root / "spk" / "dull.wav", 5, 44100, lowpass=6000)
    result = dataset_bandwidth.scan(str(root))
    assert result["samplingRateLimitedFiles"] == 0
    assert result["verdict"]["level"] in {"dull", "very-dull"}
