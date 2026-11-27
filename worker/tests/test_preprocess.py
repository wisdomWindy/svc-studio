from __future__ import annotations

import array
import json
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import preprocess


@pytest.fixture()
def work():
    import shutil
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="svcpp-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def project(work: Path) -> Path:
    root = work / "project"
    for sub in ["dataset/44k/spk", "configs", "reports"]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "configs" / "config.json").write_text(json.dumps({"model": {"vol_embedding": False}}), encoding="utf-8")
    (root / "configs" / "diffusion.yaml").write_text("data:\n  duration: 2\n", encoding="utf-8")
    return root


@pytest.fixture()
def upstream(work: Path) -> Path:
    up = work / "upstream"
    up.mkdir()
    for name in ["resample.py", "preprocess_flist_config.py", "preprocess_hubert_f0.py"]:
        (up / name).write_text("# stub\n", encoding="utf-8")
    return up


def write_tone(path: Path, seconds: float = 1.0) -> None:
    samples = array.array("h", [1000] * int(44100 * seconds))
    audio.write_wav(path, samples, 44100, 1)


def test_build_steps_arguments(project: Path, upstream: Path) -> None:
    steps = preprocess.build_steps(
        str(project),
        str(upstream),
        encoder="whisper-ppg",
        f0="fcpe",
        num_processes=2,
        use_diff=True,
        vol_aug=True,
        python="py",
    )
    assert [s["name"] for s in steps] == ["resample", "flist_config", "hubert_f0"]
    assert steps[0]["argv"][-1] == "--skip_loudnorm"
    assert steps[0]["argv"][2] == "--sr2"
    assert "--vol_aug" in steps[1]["argv"]
    assert steps[1]["argv"][steps[1]["argv"].index("--speech_encoder") + 1] == "whisper-ppg"
    assert "--use_diff" in steps[2]["argv"]
    assert steps[2]["argv"][steps[2]["argv"].index("--num_processes") + 1] == "2"
    assert steps[2]["argv"][steps[2]["argv"].index("--f0_predictor") + 1] == "fcpe"
    assert all(s["cwd"] == project for s in steps)


def test_run_chain_success_builds_manifest(project: Path, upstream: Path) -> None:
    dataset = project / "dataset" / "44k" / "spk"

    def fake_runner(argv, cwd, env):
        write_tone(dataset / "a.wav")
        write_tone(dataset / "b.wav")
        for wav in dataset.glob("*.wav"):
            (dataset / (wav.name + ".soft.pt")).write_bytes(b"x")
            (dataset / (wav.name + ".f0.npy")).write_bytes(b"x")
            (dataset / (wav.name + ".spec.pt")).write_bytes(b"x")
        return 0

    manifest = preprocess.run_chain(str(project), str(upstream), runner=fake_runner)
    assert manifest["passed"] is True
    assert manifest["failedStep"] is None
    assert len(manifest["steps"]) == 3
    assert manifest["counts"]["wav"] == 2
    assert manifest["counts"]["soft"] == 2
    assert manifest["diffs"] == []
    assert manifest["minClipSeconds"] == pytest.approx(1.0, abs=0.05)
    assert manifest["configSha256"]

    saved = project / "reports" / "preprocess_manifest.json"
    assert saved.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["schema"] == preprocess.MANIFEST_SCHEMA
    assert list(saved.parent.glob("*.tmp")) == []


def test_failure_stops_chain_and_records_exit_code(project: Path, upstream: Path) -> None:
    calls = []

    def failing_runner(argv, cwd, env):
        calls.append(argv)
        return 1 if "preprocess_flist_config.py" in " ".join(argv) else 0

    manifest = preprocess.run_chain(str(project), str(upstream), runner=failing_runner)
    assert manifest["failedStep"] == "flist_config"
    assert len(manifest["steps"]) == 2, "失败后不再执行第三步"
    assert manifest["steps"][-1]["exitCode"] == 1
    assert manifest["passed"] is False
    assert len(calls) == 2


def test_missing_products_are_reported_as_diffs(project: Path, upstream: Path) -> None:
    dataset = project / "dataset" / "44k" / "spk"

    def partial_runner(argv, cwd, env):
        write_tone(dataset / "a.wav")
        return 0

    manifest = preprocess.run_chain(str(project), str(upstream), runner=partial_runner)
    kinds = {d["kind"]: d for d in manifest["diffs"]}
    assert kinds["soft"]["expected"] == 1 and kinds["soft"]["actual"] == 0
    assert manifest["passed"] is False


def test_use_diff_expects_extra_products(project: Path, upstream: Path) -> None:
    dataset = project / "dataset" / "44k" / "spk"

    def runner(argv, cwd, env):
        write_tone(dataset / "a.wav")
        for suffix in [".soft.pt", ".f0.npy", ".spec.pt"]:
            (dataset / ("a.wav" + suffix)).write_bytes(b"x")
        return 0

    manifest = preprocess.run_chain(str(project), str(upstream), use_diff=True, runner=runner)
    kinds = {d["kind"] for d in manifest["diffs"]}
    assert {"vol", "mel", "aug_mel", "aug_vol"} <= kinds


def test_vol_embedding_config_expects_vol_npy(project: Path, upstream: Path) -> None:
    (project / "configs" / "config.json").write_text(
        json.dumps({"model": {"vol_embedding": True}}), encoding="utf-8"
    )
    dataset = project / "dataset" / "44k" / "spk"

    def runner(argv, cwd, env):
        write_tone(dataset / "a.wav")
        for suffix in [".soft.pt", ".f0.npy", ".spec.pt"]:
            (dataset / ("a.wav" + suffix)).write_bytes(b"x")
        return 0

    manifest = preprocess.run_chain(str(project), str(upstream), runner=runner)
    assert any(d["kind"] == "vol" for d in manifest["diffs"])


def test_count_products_ignores_unrelated_files(project: Path) -> None:
    dataset = project / "dataset" / "44k" / "spk"
    write_tone(dataset / "a.wav")
    (dataset / "notes.txt").write_text("x", encoding="utf-8")
    counts = preprocess.count_products(project / "dataset" / "44k")
    assert counts["wav"] == 1
    assert counts["soft"] == 0


def test_rerun_does_not_duplicate_products(project: Path, upstream: Path) -> None:
    dataset = project / "dataset" / "44k" / "spk"

    def idempotent_runner(argv, cwd, env):
        if not (dataset / "a.wav").exists():
            write_tone(dataset / "a.wav")
        for suffix in [".soft.pt", ".f0.npy", ".spec.pt"]:
            target = dataset / ("a.wav" + suffix)
            if not target.exists():
                target.write_bytes(b"x")
        return 0

    first = preprocess.run_chain(str(project), str(upstream), runner=idempotent_runner)
    second = preprocess.run_chain(str(project), str(upstream), runner=idempotent_runner)

    assert first["counts"] == second["counts"]
    assert second["counts"]["wav"] == 1
    assert second["counts"]["soft"] == 1
    assert second["passed"] is True
    assert second["diffs"] == []

def test_events_mode_streams_per_stage_progress(project: Path, upstream: Path, capsys) -> None:
    """--events 模式要按阶段/逐文件推进度，Rust 侧才能把 0% 变成动态百分比。"""
    dataset = project / "dataset" / "44k" / "spk"
    raw = project / "dataset_raw" / "spk"
    raw.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        write_tone(raw / f"{index}.wav")

    def fake_runner(argv, cwd, env):
        name = Path(argv[1]).name
        for index in range(3):
            write_tone(dataset / f"{index}.wav")
            if name == "preprocess_hubert_f0.py":
                (dataset / f"{index}.wav.soft.pt").write_bytes(b"x")
                (dataset / f"{index}.wav.f0.npy").write_bytes(b"x")
                (dataset / f"{index}.wav.spec.pt").write_bytes(b"x")
        return 0

    preprocess.run_chain(str(project), str(upstream), runner=fake_runner, events=True)

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    progress = [line for line in lines if line.get("kind") == "progress"]
    assert {item["stage"] for item in progress} == {
        "resample",
        "flist_config",
        "hubert_f0",
    }
    hubert = [item for item in progress if item["stage"] == "hubert_f0"]
    assert hubert[0]["step"] == 0
    assert (hubert[-1]["step"], hubert[-1]["total"]) == (3, 3)