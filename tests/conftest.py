"""让 `pytest worker/tests` 从仓库根运行时也能导入 svc_bridge，并提供共享临时目录。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


@pytest.fixture()
def work():
    """不用 pytest 的 tmp_path：本机临时目录 ACL 异常会让 fixture 自己报 PermissionError。"""
    path = Path(tempfile.mkdtemp(prefix="svctest-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
