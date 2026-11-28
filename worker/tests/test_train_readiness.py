"""训练前置检查：空数据集要给可理解的错误，而不是 DataLoader 的 num_samples=0。"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import train


def make_wav(path: Path, seconds: float = 1.0) -> Path:
    import array
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(44100 * seconds)
    samples = array.array(
        "h", [int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / 44100)) for i in range(n)]
    )
    audio.write_wav(path, samples, 44100, 1)
    return path


@pytest.fixture()
def proj():
    root = Path(tempfile.mkdtemp(prefix="svctrain-"))
    (root / "configs").mkdir()
    (root / "filelists").mkdir()
    (root / "dataset" / "44k" / "spk").mkdir(parents=True)
    cfg = {"data": {"training_files": "filelists/train.txt"}}
    (root / "configs" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / "filelists" / "train.txt").write_text("", encoding="utf-8")
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_dataset_is_blocked_with_clear_reason(proj: Path) -> None:
    blocked = train.dataset_readiness(proj, proj / "configs" / "config.json")
    assert blocked is not None
    assert blocked["code"] == "SVC-3003"
    assert "没有可训练的音频" in blocked["message"]
    assert "0 个 wav" in blocked["detail"]


def test_ready_dataset_passes(proj: Path) -> None:
    make_wav(proj / "dataset" / "44k" / "spk" / "a.wav")
    (proj / "filelists" / "train.txt").write_text(
        "dataset/44k/spk/a.wav|spk|0\n", encoding="utf-8"
    )
    assert train.dataset_readiness(proj, proj / "configs" / "config.json") is None


def test_audio_without_filelist_is_blocked(proj: Path) -> None:
    """有 wav 但清单是空的（没预处理）→ 仍然不能训练，否则同样是 num_samples=0。"""
    make_wav(proj / "dataset" / "44k" / "spk" / "a.wav")
    blocked = train.dataset_readiness(proj, proj / "configs" / "config.json")
    assert blocked is not None
    assert "0 行" in blocked["detail"]


def test_filelist_without_audio_is_blocked(proj: Path) -> None:
    (proj / "filelists" / "train.txt").write_text(
        "dataset/44k/spk/missing.wav|spk|0\n", encoding="utf-8"
    )
    blocked = train.dataset_readiness(proj, proj / "configs" / "config.json")
    assert blocked is not None
    assert "0 个 wav" in blocked["detail"]
