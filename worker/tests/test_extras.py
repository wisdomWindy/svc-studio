from __future__ import annotations

import json
from pathlib import Path

import pytest

from svc_bridge.commands import extras as aux


@pytest.fixture()
def fake_upstream(work: Path) -> Path:
    up = work / "upstream"
    (up / "cluster").mkdir(parents=True)
    (up / "cluster" / "train_cluster.py").write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path('logs/44k')\n"
        "p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'kmeans_10000.pt').write_bytes(b'kmeans')\n"
        "print('done')\n",
        encoding="utf-8",
    )
    (up / "train_index.py").write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path('logs/44k')\n"
        "p.mkdir(parents=True, exist_ok=True)\n"
        "(p / 'feature_and_index.pkl').write_bytes(b'index')\n",
        encoding="utf-8",
    )
    return up


def test_build_argv_shapes(work: Path) -> None:
    assert aux.build_argv("up", "cluster", python="py")[1].endswith("train_cluster.py")
    index_argv = aux.build_argv("up", "index", python="py")
    assert index_argv[1].endswith("train_index.py")
    assert index_argv[-2:] == ["-c", "configs/config.json"]
    with pytest.raises(ValueError):
        aux.build_argv("up", "nope")


def test_cluster_produces_expected_output(fake_upstream: Path, work: Path) -> None:
    project = work / "project"
    project.mkdir()
    result = aux.run_aux(str(project), str(fake_upstream), "cluster", python="python")
    assert result["ok"] is True
    assert result["outputExists"] is True
    assert (project / "logs/44k/kmeans_10000.pt").is_file()


def test_index_produces_expected_output(fake_upstream: Path, work: Path) -> None:
    project = work / "project"
    project.mkdir()
    result = aux.run_aux(str(project), str(fake_upstream), "index", python="python")
    assert result["ok"] is True
    assert (project / "logs/44k/feature_and_index.pkl").is_file()


def test_success_without_output_is_failure(work: Path) -> None:
    up = work / "up"
    (up / "cluster").mkdir(parents=True)
    (up / "cluster" / "train_cluster.py").write_text("print('noop')\n", encoding="utf-8")
    project = work / "p"
    project.mkdir()
    result = aux.run_aux(str(project), str(up), "cluster", python="python")
    assert result["ok"] is False
    assert "没有生成预期产物" in result["error"]


def test_crashing_script_reports_nonzero(work: Path) -> None:
    up = work / "up"
    (up / "cluster").mkdir(parents=True)
    (up / "cluster" / "train_cluster.py").write_text(
        "import sys; sys.stderr.write('boom'); sys.exit(3)\n", encoding="utf-8"
    )
    project = work / "p"
    project.mkdir()
    result = aux.run_aux(str(project), str(up), "cluster", python="python")
    assert result["ok"] is False
    assert result["exitCode"] == 3
    assert "boom" in result["stderrTail"]


def test_cli_writes_single_json(fake_upstream: Path, work: Path, capsysbinary) -> None:
    project = work / "p"
    project.mkdir()
    code = aux.main(["--project", str(project), "--upstream", str(fake_upstream), "--kind", "cluster"])
    assert code == 0
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert payload["kind"] == "cluster"
