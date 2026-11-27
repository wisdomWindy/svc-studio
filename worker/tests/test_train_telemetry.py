"""训练遥测：显存采样必须"读不到就 None"，绝不能影响训练。"""

from __future__ import annotations

import subprocess

import pytest

from svc_bridge.commands import train


def test_sample_vram_returns_int_or_none() -> None:
    value = train.sample_vram_mb()
    assert value is None or (isinstance(value, int) and value >= 0)


def test_sample_vram_survives_missing_nvidia_smi(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi 不存在")

    monkeypatch.setattr(subprocess, "run", boom)
    assert train.sample_vram_mb() is None


def test_sample_vram_survives_nonzero_exit(monkeypatch) -> None:
    class Proc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    assert train.sample_vram_mb() is None


def test_sample_vram_parses_first_value(monkeypatch) -> None:
    class Proc:
        returncode = 0
        stdout = "5830\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    assert train.sample_vram_mb() == 5830


def test_sample_vram_tolerates_unit_suffix(monkeypatch) -> None:
    # 某些驱动版本会输出 "5830 MiB"
    class Proc:
        returncode = 0
        stdout = "6102 MiB\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    assert train.sample_vram_mb() == 6102


@pytest.mark.parametrize("text", ["", "\n", "N/A\n"])
def test_sample_vram_tolerates_garbage(monkeypatch, text: str) -> None:
    class Proc:
        returncode = 0
        stdout = text

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    assert train.sample_vram_mb() is None
