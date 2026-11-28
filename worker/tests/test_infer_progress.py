"""推理进度：单文件也要动，且单调、封顶 95%、结尾 100。"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from svc_bridge.commands import infer, infer_cli


def _run(monkeypatch, audio_seconds: float, fake_batch) -> list[dict]:
    events: list[dict] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="infer-progress-"))

    def fake_emit(kind: str, **kw):
        events.append({"kind": kind, **kw})

    monkeypatch.setattr(infer_cli, "emit", fake_emit)
    monkeypatch.setattr(infer_cli, "emit_log", lambda *a, **k: None)
    monkeypatch.setattr(infer, "_seconds_of", lambda p: audio_seconds)
    monkeypatch.setattr(infer, "run_batch", fake_batch)

    params = tmp_dir / "params.json"
    params.write_text("{}", encoding="utf-8")
    try:
        infer_cli.main(
            [
                "--project", str(tmp_dir),
                "--model", str(tmp_dir / "G.pth"),
                "--config", str(tmp_dir / "config.json"),
                "--params", str(params),
                "--spk", "spk",
                "--inputs", str(tmp_dir / "a.wav"),
            ]
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return events


def test_single_file_progress_moves_and_caps(monkeypatch) -> None:
    """一个文件、耗时 3 秒：进度必须动起来，且中途不超过 95。"""

    def fake_batch(*args, **kwargs):
        time.sleep(3.0)
        kwargs["on_item"](1, 1, {"status": "ok"})
        return {"batch": ".", "items": [], "summary": {"ok": 1, "failed": 0}}

    events = _run(monkeypatch, 10.0, fake_batch)
    steps = [e["step"] for e in events if e["kind"] == "progress"]
    assert steps, "必须有进度事件"
    assert steps == sorted(steps), f"进度必须单调：{steps}"
    assert max(steps) <= 100
    assert any(0 < s < 95 for s in steps), f"中途应有 0–95 之间的值：{steps}"
    assert steps[-1] == 100


def test_progress_never_exceeds_current_file_ceiling(monkeypatch) -> None:
    """两个文件、估时很短：第一次冲到 100 只能发生在全部完成之后。"""

    def fake_batch(*args, **kwargs):
        time.sleep(2.0)
        kwargs["on_item"](1, 2, {"status": "ok"})
        time.sleep(1.5)
        kwargs["on_item"](2, 2, {"status": "ok"})
        return {"batch": ".", "items": [], "summary": {"ok": 2, "failed": 0}}

    events = _run(monkeypatch, 1.0, fake_batch)
    steps = [e["step"] for e in events if e["kind"] == "progress"]
    assert steps == sorted(steps), f"进度必须单调：{steps}"
    assert steps.count(100) == 1, f"只应在最后出现一次 100：{steps}"
    assert steps[-1] == 100
    # 第一个文件完成时不应直接 100
    first_50 = steps.index(50) if 50 in steps else None
    assert first_50 is None or steps.index(100) > first_50


def test_stage_events_are_emitted(monkeypatch) -> None:
    def fake_batch(*args, **kwargs):
        kwargs["on_item"](1, 1, {"status": "ok"})
        return {"batch": ".", "items": [], "summary": {"ok": 1, "failed": 0}}

    events = _run(monkeypatch, 5.0, fake_batch)
    stages = [e for e in events if e["kind"] == "stage"]
    assert stages, "应有 stage 事件（界面「事件」面板靠它）"
    assert any(e.get("stage") == "inference" for e in stages)
