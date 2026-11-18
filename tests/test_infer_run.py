from __future__ import annotations

import array
import json
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import infer


def make_wav(path: Path, seconds: float = 1.0, sample_rate: int = 44100, channels: int = 1, amplitude: float = 0.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    total = int(seconds * sample_rate)
    for i in range(total):
        v = int(32767 * amplitude * (1 if (i // 200) % 2 == 0 else -1))
        for _ in range(channels):
            samples.append(v)
    audio.write_wav(path, samples, sample_rate, channels)
    return path


def fake_runner(kwargs, output: Path) -> None:
    src = Path(kwargs["raw_audio_path"])
    wav = audio.read_wav(src)
    audio.write_wav(output, wav.samples, wav.sample_rate, 1)


def test_to_svc_kwargs_matches_upstream_names() -> None:
    kwargs = infer.to_svc_kwargs(
        {
            "trans": 2,
            "sliceDb": -30,
            "clusterInferRatio": 0.4,
            "autoPredictF0": False,
            "noiseScale": 0.5,
            "padSeconds": 0.3,
            "clip": 25,
            "linearGradient": 1,
            "f0Predictor": "rmvpe",
            "kStep": 50,
            "loudnessEnvelopeAdjustment": 0.8,
        },
        spk="xiaoman",
        raw_audio_path=Path("raw/a.wav"),
    )
    assert kwargs["spk"] == "xiaoman"
    assert kwargs["tran"] == 2
    assert kwargs["clip_seconds"] == 25.0
    assert kwargs["lg_num"] == 1.0
    assert kwargs["f0_predictor"] == "rmvpe"
    assert kwargs["k_step"] == 50
    assert kwargs["cluster_infer_ratio"] == 0.4


def test_normalize_input_converts_to_44k_mono(work: Path) -> None:
    src = make_wav(work / "in/st.wav", seconds=1.0, sample_rate=8000, channels=2, amplitude=0.95)
    dst = work / "out/n.wav"
    dst.parent.mkdir(parents=True)
    infer.normalize_input(src, dst)
    wav = audio.read_wav(dst)
    assert wav.sample_rate == 44100
    assert wav.channels == 1
    assert abs(wav.seconds - 1.0) < 0.05
    assert audio.peak_dbfs(wav.samples) <= -0.9


def test_run_batch_writes_report_and_outputs(work: Path) -> None:
    a = make_wav(work / "in/a.wav", 1.0)
    b = make_wav(work / "in/b.wav", 0.8)
    report = infer.run_batch(
        work / "proj",
        "model.pth",
        "config.json",
        {"wavFormat": "wav", "trans": 0},
        "spk",
        [a, b],
        batch_name="t1",
        runner=fake_runner,
    )
    assert report["summary"]["ok"] == 2
    assert report["summary"]["failed"] == 0
    assert report["summary"]["status"] == "succeeded"
    batch = Path(report["batch"])
    assert (batch / "report.json").is_file()
    assert (batch / "inputs" / "a.wav").is_file()
    assert (batch / "outputs" / "a.wav").is_file()
    saved = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    assert saved["paramsHash"] == report["paramsHash"]
    assert saved["spk"] == "spk"


def test_corrupt_input_is_isolated(work: Path) -> None:
    good = make_wav(work / "in/good.wav", 1.0)
    bad = work / "in/bad.wav"
    bad.write_bytes(b"not a wav")
    report = infer.run_batch(
        work / "proj",
        "m.pth",
        "c.json",
        {"wavFormat": "wav"},
        "spk",
        [good, bad],
        batch_name="mix",
        runner=fake_runner,
    )
    statuses = [i["status"] for i in report["items"]]
    assert statuses.count("ok") == 1
    assert statuses.count("failed") == 1
    assert report["summary"]["status"] == "succeeded_with_errors"


def test_retry_failed_only_reruns_failures(work: Path) -> None:
    good = make_wav(work / "in/good.wav", 1.0)
    bad = work / "in/bad.wav"
    bad.write_bytes(b"never decodes")
    report = infer.run_batch(
        work / "proj",
        "m.pth",
        "c.json",
        {"wavFormat": "wav"},
        "spk",
        [good, bad],
        batch_name="retry",
        runner=fake_runner,
    )
    assert report["summary"]["failed"] == 1

    calls = []

    def tolerant(kwargs, output):
        calls.append(kwargs["raw_audio_path"])
        fake_runner(kwargs, output)

    # retry 用批内已规范化的输入；坏文件在 prepare 阶段就被排除了，这里造一个“可重跑”的场景
    report["items"].append(
        {
            "input": str(work / "in/good.wav"),
            "output": str(Path(report["batch"]) / "outputs" / "retry.wav"),
            "status": "failed",
            "error": "boom",
        }
    )
    updated = infer.retry_failed(report, tolerant)
    # 坏文件在导入阶段就失败了（没有输出路径），无法重跑；只重跑可重跑的失败项。
    assert updated["summary"]["failed"] == 1
    assert len(calls) == 1, "只应重跑可重跑的失败项"


def test_params_hash_is_stable_and_order_independent() -> None:
    a = infer.params_hash({"trans": 0, "clip": 25})
    b = infer.params_hash({"clip": 25, "trans": 0})
    assert a == b
    assert a != infer.params_hash({"trans": 1, "clip": 25})

def test_resolve_device_prefers_explicit_and_falls_back_to_cpu() -> None:
    assert infer.resolve_device("cuda:0", cuda_available=False) == "cuda:0"
    assert infer.resolve_device(None, cuda_available=True) == "cuda"
    assert infer.resolve_device(None, cuda_available=False) == "cpu"


def test_report_records_device(work: Path) -> None:
    a = make_wav(work / "in/a.wav", 1.0)
    report = infer.run_batch(
        work / "proj", "m.pth", "c.json", {"wavFormat": "wav", "device": "cpu"}, "spk", [a],
        batch_name="dev", runner=fake_runner,
    )
    assert report["device"] == "cpu"