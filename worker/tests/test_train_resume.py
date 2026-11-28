"""续训完整性：G/D 必须成对，否则上游会静默从零开始（用户以为在续训）。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from svc_bridge.commands import train


@pytest.fixture()
def proj():
    root = Path(tempfile.mkdtemp(prefix="svcresume-"))
    (root / "logs" / "44k").mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def touch(dir_path: Path, name: str) -> None:
    (dir_path / name).write_bytes(b"x")


def test_no_checkpoints_is_fine(proj: Path) -> None:
    """首次训练：目录空的，不需要拦。"""
    assert train.checkpoint_readiness(proj, "44k") is None


def test_missing_project_dir_is_fine(proj: Path) -> None:
    assert train.checkpoint_readiness(proj / "nope", "44k") is None


def test_paired_checkpoints_are_fine(proj: Path) -> None:
    d = proj / "logs" / "44k"
    touch(d, "G_100.pth")
    touch(d, "D_100.pth")
    assert train.checkpoint_readiness(proj, "44k") is None


def test_g_without_d_is_blocked(proj: Path) -> None:
    """只有 G：上游加载 D 会抛 IndexError，随后 global_step=0 从头开始。"""
    d = proj / "logs" / "44k"
    touch(d, "G_100.pth")
    blocked = train.checkpoint_readiness(proj, "44k")
    assert blocked is not None
    assert blocked["code"] == "SVC-5005"
    assert "D_100.pth" in blocked["detail"]
    assert "从零" in blocked["message"] or "从零" in blocked["detail"]


def test_d_without_g_is_blocked(proj: Path) -> None:
    d = proj / "logs" / "44k"
    touch(d, "D_200.pth")
    blocked = train.checkpoint_readiness(proj, "44k")
    assert blocked is not None
    assert "G_200.pth" in blocked["detail"]


def test_partial_pair_among_many_is_blocked(proj: Path) -> None:
    d = proj / "logs" / "44k"
    for step in (100, 200):
        touch(d, f"G_{step}.pth")
        touch(d, f"D_{step}.pth")
    touch(d, "G_300.pth")  # 最新一步只有 G：上游会挑 D_200 当最新，但 G 已读到 300
    blocked = train.checkpoint_readiness(proj, "44k")
    assert blocked is not None
    assert "D_300.pth" in blocked["detail"]
