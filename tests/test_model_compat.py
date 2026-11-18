from __future__ import annotations

from copy import deepcopy

from svc_bridge.commands import model as compat


def base_config(**model_over):
    model = {
        "speech_encoder": "vec768l12",
        "ssl_dim": 768,
        "gin_channels": 768,
        "n_speakers": 2,
        "vocoder_name": "nsf-hifigan",
    }
    model.update(model_over)
    return {"model": model, "spk": {"a": 0, "b": 1}}


def full_shapes(ssl_dim: int = 768):
    return {
        "enc_p.emb_phone.weight": (192, ssl_dim),
        "flow.flows.0.pre.weight": (192, 192),
        "dec.conv_pre.weight": (512, 192),
        "emb_g.weight": (2, 768),
        "ref_enc.conv_layers.0.weight": (128, 768, 1),
        "enc_q.pre.weight": (192, 768),
    }


def test_normalizes_three_container_shapes() -> None:
    shapes, _ = compat.normalize_state_dict({"model": {"a.weight": _T((2, 3))}})
    assert shapes == {"a.weight": (2, 3)}

    shapes, notes = compat.normalize_state_dict(
        {"module.enc_p.emb_phone.weight": _T((192, 768))}
    )
    assert "enc_p.emb_phone.weight" in shapes
    assert any("module." in n for n in notes)


def _T(shape):
    class Tensor:
        def __init__(self, s):
            self.shape = s

    return Tensor(shape)


def test_ready_when_required_prefixes_present() -> None:
    result = compat.check_compat(base_config(), full_shapes())
    assert result["verdict"] == "ready"
    assert result["clipLimitSeconds"] == 30.0


def test_compressed_when_enc_q_missing() -> None:
    shapes = full_shapes()
    del shapes["enc_q.pre.weight"]
    result = compat.check_compat(base_config(), shapes)
    assert result["verdict"] == "compressed"
    assert any("压缩模型" in n for n in result["notes"])


def test_missing_structure_is_incompatible_with_keys() -> None:
    shapes = full_shapes()
    del shapes["enc_p.emb_phone.weight"]
    result = compat.check_compat(base_config(), shapes)
    assert result["verdict"] == "incompatible"
    assert result["code"] == "SVC-6005"
    assert "enc_p." in result["missingKeys"]


def test_ssl_dim_mismatch_is_incompatible() -> None:
    result = compat.check_compat(base_config(ssl_dim=256), full_shapes(768))
    assert result["code"] == "SVC-6001"


def test_config_without_model_is_incompatible() -> None:
    result = compat.check_compat({"spk": {}}, full_shapes())
    assert result["code"] == "SVC-4001"


def test_speaker_count_mismatch_becomes_note() -> None:
    config = base_config()
    config["spk"] = {"only": 0}
    result = compat.check_compat(config, full_shapes())
    assert any("emb_g" in n for n in result["notes"])


def test_infers_encoder_when_config_missing_it() -> None:
    config = base_config()
    config["model"].pop("speech_encoder")
    config["model"]["ssl_dim"] = 256
    shapes = full_shapes(256)
    shapes["emb_g.weight"] = (2, 256)
    result = compat.check_compat(config, shapes)
    assert result["speechEncoder"] in ("vec256l9", "hubertsoft")
    assert any("推断" in n for n in result["notes"])


def test_whisper_limit_is_25_seconds() -> None:
    config = base_config(speech_encoder="whisper-ppg", ssl_dim=1024)
    shapes = full_shapes(1024)
    result = compat.check_compat(config, shapes)
    assert result["clipLimitSeconds"] == 25.0


def test_run_check_without_torch_returns_unknown() -> None:
    result = compat.run_check("nope.pth", "nope.json")
    assert result["verdict"] in ("unknown", "incompatible")
    assert result["code"] in ("SVC-1001", "SVC-4001")


def test_check_does_not_mutate_config() -> None:
    config = base_config()
    before = deepcopy(config)
    compat.check_compat(config, full_shapes())
    assert config == before
