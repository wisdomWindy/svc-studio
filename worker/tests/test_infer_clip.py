"""推理参数默认值：clip 必须给安全上限，否则整首歌一次性推理会打爆显存。"""

from svc_bridge.commands import infer


def test_clip_defaults_to_25s() -> None:
    kwargs = infer.to_svc_kwargs({"trans": 0}, "spk", "a.wav")
    assert kwargs["clip_seconds"] == 25.0


def test_clip_zero_is_treated_as_default() -> None:
    kwargs = infer.to_svc_kwargs({"trans": 0, "clip": 0}, "spk", "a.wav")
    assert kwargs["clip_seconds"] == 25.0


def test_explicit_clip_is_respected() -> None:
    kwargs = infer.to_svc_kwargs({"trans": 0, "clip": 12}, "spk", "a.wav")
    assert kwargs["clip_seconds"] == 12.0
