"""超长素材先切段再分离：41 分钟整段分离会打满内存且没有中间进度。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from svc_bridge.commands import dataset_separate

FFMPEG = Path(r"D:\program\svcstudio\SVC Studio\resources\bin\ffmpeg.exe")


@pytest.fixture()
def work():
    root = Path(tempfile.mkdtemp(prefix="svcsplit-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def make_wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if FFMPEG.is_file():
        subprocess.run(
            [str(FFMPEG), "-v", "error", "-y", "-f", "lavfi",
             "-i", f"anullsrc=r=44100:cl=mono", "-t", str(seconds),
             "-c:a", "pcm_s16le", str(path)],
            check=True,
        )
    return path


needs_ffmpeg = pytest.mark.skipif(not FFMPEG.is_file(), reason="需要 ffmpeg")


@needs_ffmpeg
def test_probe_seconds_reads_duration(work: Path) -> None:
    wav = make_wav(work / "a.wav", 3.0)
    seconds = dataset_separate._probe_seconds(str(FFMPEG), wav)
    assert 2.5 < seconds < 3.5, seconds


@needs_ffmpeg
def test_split_long_source_produces_parts(work: Path) -> None:
    src = make_wav(work / "long.wav", 5.0)
    parts = dataset_separate._split_long_source(str(FFMPEG), src, 2.0)
    assert len(parts) >= 2, parts
    for part in parts:
        assert part.is_file()
        assert part.name.startswith("long.part")
        assert 1.5 < dataset_separate._probe_seconds(str(FFMPEG), part) < 2.6


@needs_ffmpeg
def test_short_source_is_not_split(work: Path) -> None:
    src = make_wav(work / "short.wav", 1.0)
    assert dataset_separate._split_long_source(str(FFMPEG), src, 600.0) == []


def test_max_segment_is_bounded() -> None:
    """常量必须存在且合理：太小会切得过碎，太大会重新打满内存。"""
    assert 300.0 <= dataset_separate.MAX_SEPARATE_SECONDS <= 1800.0
