from __future__ import annotations

import json
from pathlib import Path

import pytest

from svc_bridge.commands import mix, modeltool


@pytest.fixture()
def upstream(work: Path) -> Path:
    up = work / "upstream"
    up.mkdir()
    (up / "compress_model.py").write_text(
        "import sys\n"
        "args = sys.argv\n"
        "out = args[args.index('-o') + 1]\n"
        "path = __import__('pathlib').Path(out)\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "path.write_bytes(b'release')\n",
        encoding="utf-8",
    )
    (up / "onnx_export.py").write_text(
        'path = "NyaruTaffy"\n',
        encoding="utf-8",
    )
    return up


def test_build_compress_argv() -> None:
    argv = modeltool.build_compress_argv("up", "cfg.json", "in.pth", "out.pth", True, python="py")
    assert argv[0] == "py"
    assert argv[1].endswith("compress_model.py")
    assert argv[-1] == "-hf"
    assert argv[argv.index("-i") + 1] == "in.pth"


def test_run_compress_produces_output(work: Path, upstream: Path) -> None:
    project = work / "p"
    project.mkdir()
    result = modeltool.run_compress(
        str(project), str(upstream), "configs/config.json", "logs/G_1.pth", "logs/44k/release.pth"
    )
    assert result["ok"] is True
    assert result["outputExists"] is True
    assert (project / "logs/44k/release.pth").is_file()


def test_compress_failure_when_no_output(work: Path) -> None:
    up = work / "up2"
    up.mkdir()
    (up / "compress_model.py").write_text("print('noop')\n", encoding="utf-8")
    project = work / "p2"
    project.mkdir()
    result = modeltool.run_compress(str(project), str(up), "c.json", "i.pth", "o.pth")
    assert result["ok"] is False


def test_patch_onnx_script_replaces_path_without_touching_source(work: Path, upstream: Path) -> None:
    source = upstream / "onnx_export.py"
    before = source.read_text(encoding="utf-8")
    target = modeltool.patch_onnx_script(source, work / "tmp/onnx_export.py", "aziplayer")
    assert 'path = "aziplayer"' in target.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == before


def test_patch_onnx_script_fails_without_path(work: Path) -> None:
    src = work / "bad.py"
    src.write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(ValueError):
        modeltool.patch_onnx_script(src, work / "out.py", "x")


def test_run_onnx_export_reports_missing_output(work: Path, upstream: Path) -> None:
    project = work / "p3"
    project.mkdir()
    result = modeltool.run_onnx_export(str(project), str(upstream), "demo")
    assert result["ok"] is False
    assert result["experimental"] is True
    assert "实验性" in result["error"]


def test_mix_requires_two_to_eight_models() -> None:
    assert mix.validate_mix([{"path": "a"}])["passed"] is False
    nine = [{"path": str(i)} for i in range(9)]
    assert mix.validate_mix(nine)["passed"] is False


def test_mix_rejects_multi_speaker_and_mixed_structure() -> None:
    bad = [
        {"path": "a", "speakerCount": 2, "encoder": "vec768l12"},
        {"path": "b", "speakerCount": 1, "encoder": "vec256l9"},
    ]
    result = mix.validate_mix(bad)
    assert result["passed"] is False
    fields = {b["field"] for b in result["blocking"]}
    assert "models[0]" in fields
    assert "models" in fields


def test_convex_normalizes_and_linear_keeps_weights() -> None:
    models = [
        {"path": "a", "speakerCount": 1, "encoder": "vec768l12", "sslDim": 768, "ginChannels": 768},
        {"path": "b", "speakerCount": 1, "encoder": "vec768l12", "sslDim": 768, "ginChannels": 768},
    ]
    convex = mix.validate_mix(models, "convex", [1, 3])
    assert convex["passed"] is True
    assert convex["weights"] == [0.25, 0.75]

    linear = mix.validate_mix(models, "linear", [1, 3])
    assert linear["weights"] == [1.0, 3.0]


def test_mix_weight_count_and_positive_checks() -> None:
    models = [{"path": "a"}, {"path": "b"}]
    assert mix.validate_mix(models, "convex", [1])["passed"] is False
    assert mix.validate_mix(models, "convex", [1, 0])["passed"] is False
