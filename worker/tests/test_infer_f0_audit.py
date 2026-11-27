"""F0 审计：源→期望→实测的判断逻辑与升调建议（不依赖 librosa 与模型）。"""

from __future__ import annotations

import math
from pathlib import Path

from svc_bridge.commands import infer


def _audit(monkeypatch, before, after, trans, target=300.0):
    values = iter([before, after])
    monkeypatch.setattr(infer, "_f0_median", lambda *a, **k: next(values))
    return infer._f0_audit(Path("before.wav"), Path("after.wav"), trans, target)


def test_expected_is_transposed_before(monkeypatch) -> None:
    a = _audit(monkeypatch, 100.0, 100 * 2 ** (19 / 12), 19)
    assert a["beforeHz"] == 100.0
    assert a["expectedHz"] == round(100 * 2 ** (19 / 12), 1)
    assert a["applied"] is True


def test_trans_not_applied_is_detected(monkeypatch) -> None:
    # 升了 19 半音，但实测仍是源音高 → 变调没生效
    a = _audit(monkeypatch, 100.0, 102.0, 19)
    assert a["applied"] is False


def test_suggests_semitones_to_target(monkeypatch) -> None:
    a = _audit(monkeypatch, 100.0, 100.0, 0, target=300.0)
    # 100 → 300 需要 19.02 半音
    assert a["suggestTransSemitones"] == round(12 * math.log2(300 / 100))
    assert a["transSemitones"] == 0


def test_no_suggestion_when_already_close(monkeypatch) -> None:
    a = _audit(monkeypatch, 300.0, 300.0, 0, target=300.0)
    assert a["suggestTransSemitones"] is None


def test_handles_unmeasurable_audio(monkeypatch) -> None:
    a = _audit(monkeypatch, None, None, 12)
    assert a["beforeHz"] is None
    assert a["expectedHz"] is None
    assert a["applied"] is None


def test_trans_zero_never_claims_applied(monkeypatch) -> None:
    a = _audit(monkeypatch, 250.0, 250.0, 0)
    # trans=0 时没有"是否生效"的问题，applied 应为 True（期望=源，实测相符）
    assert a["applied"] is True

def test_detects_model_re_registering_pitch(monkeypatch) -> None:
    """trans=0 但输出从 82Hz 跳到 271Hz：模型自己把音高搬进了它的音域。"""
    a = _audit(monkeypatch, 82.0, 271.0, 0)
    assert a["applied"] is False
    assert a["registerMismatch"] is True
    assert a["shiftSemitones"] == round(12 * math.log2(271 / 82), 1)


def test_suggestion_uses_measured_landing_point(monkeypatch) -> None:
    """模型已把音高搬到 271Hz 时，建议应基于 271 而不是源 82（否则会建议 +22）。"""
    a = _audit(monkeypatch, 82.0, 271.0, 0, target=300.0)
    assert a["suggestTransSemitones"] == round(12 * math.log2(300 / 271))


def test_no_register_mismatch_when_transposed_normally(monkeypatch) -> None:
    a = _audit(monkeypatch, 100.0, 100 * 2 ** (12 / 12), 12)
    assert a["applied"] is True
    assert a["registerMismatch"] is False
