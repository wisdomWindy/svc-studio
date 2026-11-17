"""Rust 侧进程宿主测试用的假 worker。

用法：python fake_worker.py <scenario>
场景：ok / crash / flood / hang / echo / tree
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def emit(**fields):
    fields.setdefault("v", 1)
    sys.stdout.write(json.dumps(fields, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "ok"
    emit(kind="hello", protocol=1, bridge="fake", job_id=os.environ.get("JOB_ID", "test"))

    if scenario == "ok":
        for i in range(1, 4):
            emit(kind="progress", stage="demo", step=i, total=3)
        emit(kind="result", data={"ok": True})
        return 0

    if scenario == "crash":
        emit(kind="log", level="info", message="about to crash")
        return 3

    if scenario == "flood":
        for i in range(2000):
            emit(kind="log", level="debug", message=f"line {i}")
        emit(kind="result", data={"n": 2000})
        return 0

    if scenario == "hang":
        while True:
            time.sleep(0.2)

    if scenario == "tree":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        emit(kind="log", level="info", message=f"child={child.pid}")
        while True:
            time.sleep(0.2)

    if scenario == "echo":
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            cmd = msg.get("cmd")
            if cmd == "stop":
                emit(kind="stopped")
                return 0
            if cmd == "cancel":
                emit(kind="cancelled")
                return 0
            if cmd == "ping":
                emit(kind="pong")
                continue
            emit(kind="log", level="warn", message=f"unknown cmd {cmd}")
        return 0

    emit(kind="error", code="SVC-1001", message=f"unknown scenario {scenario}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
