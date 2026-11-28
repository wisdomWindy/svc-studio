"""QC 门禁：空数据集必须阻断，结构问题必须并入 QC 结论。

起因：界面出现「门禁结论：通过」但下面写着「总时长低于 30 分钟」，
而扫描结果里明明有阻断项（没有任何说话人目录）——两处结论互相矛盾。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset


def make_wav(path: Path, seconds: float, sample_rate: int = 44100) -> Path:
    import array
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sample_rate * seconds)
    samples = array.array(
        "h", [int(0.2 * 32767 * math.sin(2 * math.pi * 220 * i / sample_rate)) for i in range(n)]
    )
    audio.write_wav(path, samples, sample_rate, 1)
    return path


@pytest.fixture()
def root():
    work = Path(tempfile.mkdtemp(prefix="svcqc-"))
    r = work / "dataset_raw"
    r.mkdir()
    try:
        yield r
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_empty_dataset_is_blocking_not_passed(root: Path) -> None:
    report = dataset.qc(str(root))
    assert report["summary"]["fileCount"] == 0
    assert report["assertions"]["passed"] is False, "空数据集不能算通过"
    codes = [b["code"] for b in report["assertions"]["blocking"]]
    assert "SVC-3003" in codes, codes
    assert any("没有任何可质检的音频" in b["message"] for b in report["assertions"]["blocking"])
    # 空数据集不再重复报"总时长低于30分钟"
    codes_warn = [w["code"] for w in report["assertions"]["warnings"]]
    assert "SVC-3004" not in codes_warn, codes_warn


def test_scan_blocking_is_merged_into_qc(root: Path) -> None:
    """多层嵌套目录：scan 会阻断，QC 必须同样阻断（旧实现只报"通过"）。"""
    make_wav(root / "spk" / "a.wav", 3.0)
    (root / "spk" / "nested" / "deeper").mkdir(parents=True)
    make_wav(root / "spk" / "nested" / "deeper" / "b.wav", 3.0)

    scan_report = dataset.scan(str(root))
    assert scan_report["blocking"], "前提：结构扫描应报阻断"

    report = dataset.qc(str(root))
    assert report["assertions"]["passed"] is False
    scan_codes = {b["code"] for b in scan_report["blocking"]}
    qc_codes = {b["code"] for b in report["assertions"]["blocking"]}
    assert scan_codes <= qc_codes, f"扫描阻断 {scan_codes} 未并入 QC {qc_codes}"


def test_small_but_valid_dataset_passes_with_warning(root: Path) -> None:
    """有数据但不足 30 分钟：通过 + 一条提示（可显式豁免），不是阻断。"""
    make_wav(root / "spk" / "a.wav", 3.0)
    report = dataset.qc(str(root))
    assert report["assertions"]["passed"] is True
    assert report["assertions"]["blocking"] == []
    warnings = report["assertions"]["warnings"]
    assert any(w["code"] == "SVC-3004" for w in warnings), warnings
    assert any("分钟" in w["message"] for w in warnings), "提示里应写明实际分钟数"
