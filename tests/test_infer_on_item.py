"""回归：run_batch 必须接受 on_item 回调（CLI 会传它），且成功/失败都要回调。"""

from __future__ import annotations

import array
import shutil
import tempfile
from pathlib import Path

from svc_bridge import audio
from svc_bridge.commands import infer


def _work() -> Path:
    return Path(tempfile.mkdtemp(prefix="svcinfer-"))


def _make_wav(path: Path, seconds: float = 0.5, sample_rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    for i in range(int(seconds * sample_rate)):
        samples.append(32767 // 2 if (i // 100) % 2 == 0 else -(32767 // 2))
    audio.write_wav(path, samples, sample_rate, 1)
    return path


def _runner(kwargs, output: Path) -> None:
    wav = audio.read_wav(Path(kwargs["raw_audio_path"]))
    audio.write_wav(output, wav.samples, wav.sample_rate, 1)


def test_run_batch_calls_on_item() -> None:
    work = _work()
    try:
        src = _make_wav(work / "in" / "a.wav")
        seen: list[tuple[int, int, str]] = []
        report = infer.run_batch(
            work / "proj",
            "model.pth",
            "config.json",
            {"wavFormat": "wav", "trans": 0},
            "spk",
            [src],
            batch_name="t-on-item",
            runner=_runner,
            on_item=lambda done, total, item: seen.append((done, total, item["status"])),
        )
        assert report["summary"]["ok"] == 1
        assert seen == [(1, 1, "ok")], seen
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_run_batch_reports_failed_item() -> None:
    work = _work()
    try:
        src = _make_wav(work / "in" / "b.wav")
        seen: list[str] = []

        def boom(kwargs, output):
            raise RuntimeError("boom")

        report = infer.run_batch(
            work / "proj",
            "model.pth",
            "config.json",
            {"wavFormat": "wav", "trans": 0},
            "spk",
            [src],
            batch_name="t-fail",
            runner=boom,
            on_item=lambda done, total, item: seen.append(item["status"]),
        )
        assert report["summary"]["failed"] == 1
        assert seen == ["failed"], seen
    finally:
        shutil.rmtree(work, ignore_errors=True)
