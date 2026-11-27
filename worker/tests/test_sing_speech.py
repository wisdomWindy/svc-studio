"""说话/歌唱判别：指标定义 + 阈值标定结果 + 分类行为。"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import sing_speech

np = pytest.importorskip("numpy")
# F0 提取依赖 librosa（随运行时分发）；系统 python 没有时跳过
pytest.importorskip("librosa")


def _write(path: Path, seconds: float, kind: str) -> Path:
    sr = 22050
    n = int(seconds * sr)
    x = np.zeros(n)
    rng = np.random.default_rng(0)
    if kind == "sustain":
        # 稳定长音（模拟拖腔）
        x = 0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)
    elif kind == "speech":
        # 快速变化的音高 + 停顿（模拟说话）
        for i in range(n):
            f = 180 + 80 * math.sin(2 * math.pi * 3.5 * i / sr)
            x[i] = 0.3 * math.sin(2 * math.pi * f * i / sr)
        for k in range(0, n, sr // 2):
            x[k : k + sr // 8] = 0.0
    path.parent.mkdir(parents=True, exist_ok=True)
    audio.write_wav(path, array.array("h", (x * 32767).astype("int16").tobytes()), sr, 1)
    return path


def test_sustained_tone_scores_high(work: Path) -> None:
    m = sing_speech.analyze(_write(work / "a.wav", 5, "sustain"))
    assert m["sustainedRatio"] > 0.8, m
    assert sing_speech.classify(m) == "singing"


def test_speech_like_scores_low(work: Path) -> None:
    m = sing_speech.analyze(_write(work / "b.wav", 6, "speech"))
    assert m["sustainedRatio"] < 0.6, m
    assert sing_speech.classify(m) == "speech"


def test_threshold_matches_calibration() -> None:
    """阈值来自两类真实语料标定（AUC 0.907，TPR 80% / FPR 6%），改动需重新标定。"""
    assert sing_speech.SUSTAINED_THRESHOLD == pytest.approx(0.67)


def test_classify_boundary() -> None:
    assert sing_speech.classify({"sustainedRatio": 0.66}) == "speech"
    assert sing_speech.classify({"sustainedRatio": 0.68}) == "singing"


def test_scan_counts_both_classes(work: Path) -> None:
    root = work / "dataset_raw"
    _write(root / "spk" / "sing.wav", 5, "sustain")
    _write(root / "spk" / "spk.wav", 6, "speech")
    result = sing_speech.scan(str(root))
    assert result["files"] == 2
    assert result["singing"] == 1
    assert result["speech"] == 1
    assert result["threshold"] == pytest.approx(0.67)


def test_scan_empty(work: Path) -> None:
    result = sing_speech.scan(str(work / "nope"))
    assert result["files"] == 0 and result["entries"] == []
