"""分离的跨进程互斥：并发分离会把 GPU/内存打满（实测两个进程各 7GB、互相拖慢）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from svc_bridge.commands import dataset_separate


@pytest.fixture()
def dataset_dir():
    work = Path(tempfile.mkdtemp(prefix="svclock-"))
    base = work / "dataset_raw"
    (base / "spk").mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_acquire_and_release_roundtrip(dataset_dir: Path) -> None:
    lock = dataset_separate.acquire_separate_lock(dataset_dir)
    assert lock.is_file()
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    dataset_separate._release_separate_lock(lock)
    assert not lock.exists()


def test_same_process_can_reacquire(dataset_dir: Path) -> None:
    """同一进程重复进入不算冲突（避免重入时把自己锁死）。"""
    first = dataset_separate.acquire_separate_lock(dataset_dir)
    second = dataset_separate.acquire_separate_lock(dataset_dir)
    assert first == second
    dataset_separate._release_separate_lock(second)


def test_other_live_process_blocks(dataset_dir: Path) -> None:
    """另一个活着的进程持锁时必须拒绝。"""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        lock = dataset_dir.parent / ".svcstudio" / "separate.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(proc.pid), encoding="utf-8")
        with pytest.raises(RuntimeError) as excinfo:
            dataset_separate.acquire_separate_lock(dataset_dir)
        assert str(proc.pid) in str(excinfo.value)
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_stale_lock_is_taken_over(dataset_dir: Path) -> None:
    """持锁进程已死（陈旧锁）要能接管，否则用户永远无法再处理。"""
    lock = dataset_dir.parent / ".svcstudio" / "separate.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999", encoding="utf-8")  # 不存在的 PID
    got = dataset_separate.acquire_separate_lock(dataset_dir)
    assert got.read_text(encoding="utf-8").strip() == str(os.getpid())
    dataset_separate._release_separate_lock(got)


def test_release_does_not_remove_someone_elses_lock(dataset_dir: Path) -> None:
    lock = dataset_dir.parent / ".svcstudio" / "separate.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("123456", encoding="utf-8")
    dataset_separate._release_separate_lock(lock)  # 不是自己的锁
    assert lock.exists()
