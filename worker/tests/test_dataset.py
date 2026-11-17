from __future__ import annotations

import array
import json
import math
import shutil
import tempfile
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset


def make_wav(
    path: Path,
    seconds: float,
    sample_rate: int = 44100,
    freq: float = 440.0,
    amplitude: float = 0.5,
    channels: int = 1,
    silence_spans=None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * sample_rate)
    silence_spans = silence_spans or []
    samples = array.array("h")
    for i in range(total):
        t = i / sample_rate
        silent = any(start <= t < end for start, end in silence_spans)
        value = 0 if silent else int(32767 * amplitude * math.sin(2 * math.pi * freq * t))
        for _ in range(channels):
            samples.append(value)
    audio.write_wav(path, samples, sample_rate, channels)
    return path


def square_wav(path: Path, seconds: float, sample_rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    for i in range(int(seconds * sample_rate)):
        samples.append(32767 if (i // 100) % 2 == 0 else -32768)
    audio.write_wav(path, samples, sample_rate, 1)
    return path


@pytest.fixture()
def work():
    path = Path(tempfile.mkdtemp(prefix="svcds-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def raw_root(work: Path) -> Path:
    root = work / "dataset_raw"
    (root / "spk").mkdir(parents=True)
    return root


def test_scan_reports_structure_problems(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "a.wav", 1.0)
    (raw_root / "spk" / "nested").mkdir()
    (raw_root / "spk" / "b.mp3").write_bytes(b"ID3")
    (raw_root / "empty").mkdir()
    (raw_root / "spk" / "bad.wav").write_bytes(b"not a wav")

    report = dataset.scan(str(raw_root))
    assert "spk" in report["speakers"]
    assert report["emptyDirs"] == ["empty"]
    assert any("spk/nested" in n for n in report["nested"])
    assert report["nonWav"][0]["relPath"] == "spk/b.mp3"
    assert any(f["status"] == "corrupt" for f in report["files"])
    assert report["blocking"], "结构问题必须产生阻断项"


def test_scan_missing_root_is_blocking(work: Path) -> None:
    report = dataset.scan(str(work / "nope"))
    assert report["blocking"][0]["code"] == "SVC-3002"


def test_qc_metrics_are_consistent(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "a.wav", 3.0, sample_rate=44100)
    make_wav(raw_root / "spk" / "b.wav", 2.0, sample_rate=48000)

    report = dataset.qc(str(raw_root))
    assert report["summary"]["fileCount"] == 2
    assert abs(report["summary"]["totalSeconds"] - 5.0) < 0.05
    assert report["metrics"]["sampleRate"] == {"44100": 1, "48000": 1}
    assert report["metrics"]["channels"] == {"1": 2}
    assert report["metrics"]["duration"]["max"] == pytest.approx(3.0, abs=0.01)
    assert report["metrics"]["loudness"]["method"] == "rms"
    # 采样率不统一必须阻断
    assert any("采样率" in b["message"] for b in report["assertions"]["blocking"])


def test_qc_detects_silence_and_blocks(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "quiet.wav", 4.0, silence_spans=[(0.0, 3.0)])
    report = dataset.qc(str(raw_root))
    assert report["metrics"]["silenceRatio"] > 0.5
    assert any("静音" in b["message"] for b in report["assertions"]["blocking"])


def test_qc_detects_clipping_and_blocks(raw_root: Path) -> None:
    square_wav(raw_root / "spk" / "hot.wav", 1.0)
    report = dataset.qc(str(raw_root))
    assert report["metrics"]["clippingRatio"] > 0.001
    assert any("削波" in b["message"] for b in report["assertions"]["blocking"])


def test_over_30s_is_blocking(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "long.wav", 31.0)
    report = dataset.qc(str(raw_root))
    assert any("30s" in b["message"] for b in report["assertions"]["blocking"])


def test_clean_dataset_passes(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "ok.wav", 8.0)
    report = dataset.qc(str(raw_root))
    assert report["assertions"]["passed"] is True
    assert report["summary"]["blocking"] == 0


def test_slice_produces_chunks_and_quarantines_original(raw_root: Path, work: Path) -> None:
    long = make_wav(raw_root / "spk" / "long.wav", 22.0)
    result = dataset.slice_tree(str(raw_root), 5.0, 15.0)

    outputs = result["outputs"]["spk/long.wav"]
    assert len(outputs) >= 2
    chunks = sorted((raw_root / "spk").glob("long_*.wav"))
    assert len(chunks) == len(outputs)
    for chunk in chunks:
        seconds = audio.read_wav(chunk).seconds
        assert 4.9 <= seconds <= 15.1, f"{chunk.name} 时长 {seconds}"

    assert not long.exists(), "原文件必须移入 quarantine"
    assert (work / "quarantine" / "spk" / "long.wav").exists()

    # 幂等：再跑一次没有可切文件
    again = dataset.slice_tree(str(raw_root), 5.0, 15.0)
    assert again["outputs"] == {}


def test_resample_converts_rate_channels_and_peak(raw_root: Path) -> None:
    make_wav(raw_root / "spk" / "st.wav", 2.0, sample_rate=8000, channels=2, amplitude=0.95)
    result = dataset.resample_tree(str(raw_root))
    assert result["changed"], "应记录被转换的文件"

    wav = audio.read_wav(raw_root / "spk" / "st.wav")
    assert wav.sample_rate == 44100
    assert wav.channels == 1
    assert abs(wav.seconds - 2.0) < 0.05
    peak = audio.peak_dbfs(wav.samples)
    assert peak <= -0.9, f"峰值 {peak} 必须被压到 -1dBFS 以下"


def test_write_report_is_atomic(work: Path) -> None:
    target = work / "reports" / "qc_report.json"
    dataset.write_report({"schema": "x"}, str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == "x"
    leftovers = list((work / "reports").glob("*.tmp"))
    assert leftovers == []


def test_cli_qc_returns_nonzero_when_blocked(raw_root: Path, capsysbinary) -> None:
    make_wav(raw_root / "spk" / "long.wav", 31.0)
    code = dataset.main(["qc", "--root", str(raw_root)])
    assert code != 0
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert payload["summary"]["blocking"] >= 1
