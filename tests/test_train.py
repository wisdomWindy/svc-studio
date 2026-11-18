from __future__ import annotations

import json
from pathlib import Path

import pytest

from svc_bridge.commands import train

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_train.py"


@pytest.fixture()
def project():
    import shutil
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="svctrain-"))
    (path / "configs").mkdir(parents=True)
    (path / "reports").mkdir(parents=True)
    (path / "configs" / "config.json").write_text(
        json.dumps({"train": {"eval_interval": 800, "log_interval": 200, "keep_ckpts": 3}, "model": {}}),
        encoding="utf-8",
    )
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_build_argv_main_and_diffusion() -> None:
    main_argv = train.build_argv("up", "cfg.json", "44k", python="py")
    assert main_argv[0] == "py"
    assert main_argv[1].endswith("train.py")
    assert main_argv[-2:] == ["-m", "44k"]

    diff_argv = train.build_argv("up", "diff.yaml", "44k", python="py", kind="diffusion")
    assert diff_argv[1].endswith("train_diff.py")
    assert "-m" not in diff_argv


def test_smoke_config_overrides_without_touching_source(project: Path) -> None:
    source = project / "configs" / "config.json"
    before = source.read_text(encoding="utf-8")
    doc = train.build_smoke_config(str(source), str(project / "reports" / "smoke.json"))
    assert doc["train"]["eval_interval"] == 50
    assert doc["train"]["log_interval"] == 10
    assert doc["train"]["keep_ckpts"] == 2
    assert source.read_text(encoding="utf-8") == before
    assert (project / "reports" / "smoke.json").is_file()


def test_smoke_stops_at_max_steps_and_emits_progress(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("SVCSTUDIO_FAKE_MODE", "ok")
    events = []
    data = train.run_train(
        str(project),
        "unused",
        "configs/config.json",
        "_smoke",
        max_steps=100,
        emit=events.append,
        train_script=str(FIXTURE),
    )
    assert data["stoppedForSmoke"] is True
    # 冒烟到步数上限是我们主动终止的，必须按成功处理（Windows 下终止码是正数）。
    assert data["exitCode"] == 0
    assert data["lastStep"] >= 100
    progress = [e for e in events if e.get("kind") == "progress"]
    assert progress, "必须发出进度事件"
    assert progress[-1]["step"] >= 100
    assert events[-1]["kind"] == "result"
    assert data["elapsedSeconds"] < 30


def test_oom_is_detected_and_reported(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("SVCSTUDIO_FAKE_MODE", "oom")
    events = []
    data = train.run_train(
        str(project), "unused", "configs/config.json", "44k", emit=events.append, train_script=str(FIXTURE)
    )
    assert data["oom"] is True
    assert any(e.get("code") == "SVC-5001" for e in events)


def test_nan_is_detected_as_diverged(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("SVCSTUDIO_FAKE_MODE", "nan")
    events = []
    data = train.run_train(
        str(project), "unused", "configs/config.json", "44k", emit=events.append, train_script=str(FIXTURE)
    )
    assert data["diverged"] is True
    assert any(e.get("code") == "SVC-5002" for e in events)


def test_parse_step_ignores_malformed_lines() -> None:
    assert train._parse_step("Losses: [1.0, 2.0], step: 7")["step"] == 7
    assert train._parse_step("hello world") is None
    assert train._parse_step("Losses: [abc], step: 7") is None


def test_result_reports_elapsed_and_model_dir(project: Path, monkeypatch) -> None:
    monkeypatch.setenv("SVCSTUDIO_FAKE_MODE", "ok")
    data = train.run_train(
        str(project), "unused", "configs/config.json", "_smoke", max_steps=5, train_script=str(FIXTURE)
    )
    assert data["modelDir"] == "_smoke"
    assert data["elapsedSeconds"] >= 0
    assert data["finishedAt"]
