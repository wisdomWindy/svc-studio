from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from svc_bridge.commands import config as cfg


def base_config():
    return {
        "train": {
            "batch_size": 2,
            "epochs": 100,
            "learning_rate": 1e-4,
            "keep_ckpts": 3,
            "all_in_mem": False,
            "fp16_run": True,
        },
        "model": {
            "vocoder_name": "nsf-hifigan",
            "ssl_dim": 768,
            "speech_encoder": "vec768l12",
        },
        "data": {"sampling_rate": 44100},
        "spk": {"xiaoman": 0},
    }


def base_diffusion():
    return {
        "data": {"duration": 2},
        "train": {"batch_size": 8},
        "model": {"timesteps": 1000, "k_step_max": 0},
        "vocoder": {"type": "nsf-hifigan"},
    }


def test_valid_pair_passes() -> None:
    assert cfg.validate_config(base_config()) == []
    assert cfg.validate_diffusion(base_diffusion()) == []
    result = cfg.assert_ready(base_config(), base_diffusion(), min_clip_seconds=4.0)
    assert result["passed"] is True


def test_missing_and_out_of_range_fields_have_paths() -> None:
    bad = base_config()
    del bad["train"]["batch_size"]
    errors = cfg.validate_config(bad)
    assert errors[0]["path"] == "train.batch_size"

    bad = base_config()
    bad["train"]["batch_size"] = 64
    assert any("1–32" in e["message"] for e in cfg.validate_config(bad))

    bad = base_config()
    bad["data"]["sampling_rate"] = 48000
    assert any("44100" in e["message"] for e in cfg.validate_config(bad))

    bad = base_config()
    bad["model"]["ssl_dim"] = 256
    assert any("ssl_dim" in e["path"] for e in cfg.validate_config(bad))


def test_diffusion_k_step_must_be_less_than_timesteps() -> None:
    bad = base_diffusion()
    bad["model"]["k_step_max"] = 1000
    errors = cfg.validate_diffusion(bad)
    assert any(e["code"] == "SVC-4002" for e in errors)


def test_duration_must_be_shorter_than_min_clip() -> None:
    diff = base_diffusion()
    diff["data"]["duration"] = 3.0
    result = cfg.assert_ready(base_config(), diff, min_clip_seconds=2.4)
    assert not result["passed"]
    assert any("最短" in b["message"] for b in result["blocking"])


def test_whisper_encoder_caps_clip_at_25s() -> None:
    config = base_config()
    config["model"]["speech_encoder"] = "whisper-ppg"
    config["model"]["ssl_dim"] = 1024
    result = cfg.assert_ready(config, base_diffusion(), min_clip_seconds=27.0)
    assert not result["passed"]
    assert any("25" in b["message"] for b in result["blocking"])


def test_empty_spk_is_blocking() -> None:
    config = base_config()
    config["spk"] = {}
    result = cfg.assert_ready(config, base_diffusion(), min_clip_seconds=2.0)
    assert any(b["path"] == "spk" for b in result["blocking"])


def test_assert_ready_does_not_mutate_inputs() -> None:
    config = deepcopy(base_config())
    diff = deepcopy(base_diffusion())
    before = json.dumps([config, diff], sort_keys=True)
    cfg.assert_ready(config, diff, 3.0)
    assert json.dumps([config, diff], sort_keys=True) == before


def test_cli_validate_exit_codes(work: Path, capsysbinary) -> None:
    config_path = work / "config.json"
    diffusion_path = work / "diffusion.yaml"
    config_path.write_text(json.dumps(base_config()), encoding="utf-8")
    import yaml

    diffusion_path.write_text(yaml.safe_dump(base_diffusion()), encoding="utf-8")

    assert cfg.main(["validate", "--config", str(config_path), "--diffusion", str(diffusion_path)]) == 0
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert payload["passed"] is True

    broken = base_diffusion()
    broken["model"]["k_step_max"] = 5000
    diffusion_path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    assert cfg.main(["validate", "--config", str(config_path), "--diffusion", str(diffusion_path)]) == 2
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert payload["passed"] is False
