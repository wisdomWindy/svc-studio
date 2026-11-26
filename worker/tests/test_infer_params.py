from __future__ import annotations

from copy import deepcopy

from svc_bridge.commands import params


def norm(**over):
    base = dict(
        encoder="vec768l12",
        material_type="speech",
        has_cluster=False,
        has_index=False,
        has_diffusion=False,
    )
    base.update(over)
    return base


def test_defaults_are_applied() -> None:
    result = params.normalize({}, **norm())
    assert result["effective"]["kStep"] == 100
    assert result["effective"]["wavFormat"] == "flac"
    assert result["blocking"] == []


def test_whisper_forces_clip_and_gradient() -> None:
    result = params.normalize(
        {"clip": 0, "linearGradient": 0}, **norm(encoder="whisper-ppg")
    )
    assert result["effective"]["clip"] == 25.0
    assert result["effective"]["linearGradient"] == 1.0
    assert len(result["warnings"]) == 2


def test_singing_disables_auto_pitch() -> None:
    result = params.normalize(
        {"autoPredictF0": True}, **norm(material_type="singing")
    )
    assert result["effective"]["autoPredictF0"] is False
    assert any(w["field"] == "autoPredictF0" for w in result["warnings"])


def test_shallow_diffusion_disables_enhance() -> None:
    result = params.normalize(
        {"shallowDiffusion": True, "enhance": True},
        **norm(has_diffusion=True),
    )
    assert result["effective"]["enhance"] is False


def test_feature_retrieval_requires_index() -> None:
    result = params.normalize(
        {"featureRetrieval": True, "clusterModelPath": "x.pkl"},
        **norm(has_index=False),
    )
    assert any(b["code"] == "SVC-6003" for b in result["blocking"])


def test_ratio_without_cluster_is_blocking() -> None:
    result = params.normalize({"clusterInferRatio": 0.5}, **norm())
    assert any(b["field"] == "clusterInferRatio" for b in result["blocking"])


def test_zero_ratio_clears_model_path() -> None:
    result = params.normalize(
        {"clusterInferRatio": 0, "clusterModelPath": "kmeans.pt"},
        **norm(has_cluster=True),
    )
    assert result["effective"]["clusterModelPath"] == ""


def test_only_diffusion_requires_model() -> None:
    result = params.normalize({"onlyDiffusion": True}, **norm())
    assert any(b["field"] == "onlyDiffusion" for b in result["blocking"])


def test_out_of_range_values_are_blocked() -> None:
    result = params.normalize(
        {"trans": 99, "sliceDb": -5, "noiseScale": 2, "wavFormat": "mp3"},
        **norm(),
    )
    fields = {b["field"] for b in result["blocking"]}
    assert {"trans", "sliceDb", "noiseScale", "wavFormat"} <= fields


def test_ratio_zero_point_five_with_cluster_is_allowed() -> None:
    result = params.normalize(
        {"clusterInferRatio": 0.5, "clusterModelPath": "kmeans.pt"},
        **norm(has_cluster=True),
    )
    assert result["blocking"] == []
    assert result["effective"]["clusterModelPath"] == "kmeans.pt"


def test_input_is_not_mutated() -> None:
    payload = {"clip": 0, "autoPredictF0": True}
    before = deepcopy(payload)
    params.normalize(payload, **norm(encoder="whisper-ppg", material_type="singing"))
    assert payload == before


def test_none_predictor_is_kept_and_warned() -> None:
    result = params.normalize({"f0Predictor": "none"}, **norm())
    assert result["effective"]["f0Predictor"] == "none"
    assert any(w["code"] == "SVC-6011" for w in result["warnings"])
    assert result["blocking"] == []


def test_unknown_predictor_falls_back_to_model_default() -> None:
    result = params.normalize({"f0Predictor": "bogus"}, **norm())
    assert result["effective"]["f0Predictor"] is None
