"""DNSMOS 感知评分：只报告，且只在模型+onnxruntime 可用时跑。

单独放一个文件是因为它依赖外部 ONNX 模型（resources/pretrain/dnsmos）。
系统 python 没有 onnxruntime，因此整文件跳过；运行时由 p2_selfcheck 覆盖。
"""

from __future__ import annotations

import pytest

from svc_bridge.commands import perceptual

pytest.importorskip("onnxruntime")
if not perceptual.available():
    pytest.skip("缺少 DNSMOS 模型", allow_module_level=True)


def test_score_file_returns_all_metrics() -> None:
    from pathlib import Path

    ref = Path(r"D:\study\feiyu-voice\big_fat_fish_reference.wav")
    if not ref.is_file():
        pytest.skip("参考音不存在")
    out = perceptual.score_file(ref)
    assert out is not None
    for key in ("sig", "bak", "ovrl", "p808", "segments"):
        assert isinstance(out[key], (int, float))
    assert 0.5 <= out["ovrl"] <= 5.0
    assert out["segments"] >= 1


def test_scan_reports_availability() -> None:
    result = perceptual.scan(r"D:\study\feiyu-voice\big_fat_fish_audio_dataset")
    assert result["available"] is True
    assert result["files"] >= 1
    assert "medianOvrl" in result["summary"]
