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
