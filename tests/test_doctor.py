from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from svc_bridge.commands import doctor

EXPECTED_IDS = ["python", "torch", "cuda", "gpu", "ffmpeg", "disk", "encoder", "f0", "vocoder"]


@pytest.fixture()
def workdir():
    """不用 pytest 的 tmp_path：本机临时目录 ACL 异常会让 fixture 自己报 PermissionError。"""
    path = Path(tempfile.mkdtemp(prefix="svcdoctor-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_report_has_nine_items_and_consistent_summary(workdir: Path) -> None:
    report = doctor.build_report(str(workdir))
    assert report["schema"] == doctor.SCHEMA
    assert [i["id"] for i in report["items"]] == EXPECTED_IDS

    summary = report["summary"]
    assert summary["pass"] + summary["warn"] + summary["fail"] == 9
    blocking_fail = any(i["state"] == "fail" and i["blocking"] for i in report["items"])
    assert summary["trainable"] is (not blocking_fail)


def test_missing_weights_are_blocking_with_actionable_fix(workdir: Path) -> None:
    report = doctor.build_report(str(workdir))
    for check_id in ("encoder", "f0", "vocoder"):
        item = next(i for i in report["items"] if i["id"] == check_id)
        assert item["state"] == "fail"
        assert item["fix"], "失败必须给出可操作的修复动作"
        assert item["detail"]["missing"]


def test_weights_present_turns_check_green(workdir: Path) -> None:
    for rel in doctor.VOCoder_FILES:
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    item = doctor._check_files("vocoder", str(workdir), doctor.VOCoder_FILES, "声码器")
    assert item["state"] == "pass"


def test_ffmpeg_missing_and_present(monkeypatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    item = doctor.check_ffmpeg()
    assert item["state"] == "fail"
    assert "ffmpeg" in item["fix"].lower()

    monkeypatch.setattr(doctor.shutil, "which", lambda _name: r"C:\tools\ffmpeg.exe")
    assert doctor.check_ffmpeg()["state"] == "pass"


def test_disk_below_threshold_is_warning(monkeypatch, workdir: Path) -> None:
    class Usage:
        free = 1 * 1024**3

    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _p: Usage())
    item = doctor.check_disk(str(workdir))
    assert item["state"] == "warn"
    assert item["detail"]["free_gb"] == 1.0


def test_python_check_never_crashes() -> None:
    item = doctor.check_python()
    assert item["id"] == "python"
    assert item["state"] in {"pass", "warn"}


def test_cli_prints_single_json_object(workdir: Path, capsysbinary) -> None:
    code = doctor.main(["--json", "--data-root", str(workdir)])
    assert code == 0
    raw = capsysbinary.readouterr().out
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    payload = json.loads(raw.decode("utf-8"))
    assert payload["schema"] == doctor.SCHEMA
    assert payload["items"][0]["id"] == "python"
