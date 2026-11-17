"""可选训练任务：聚类（kmeans）与特征检索索引（W6-7）。

两者都是"跑一个上游脚本 + 检查产物"，不需要事件流；失败只影响该能力，不影响主模型。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import exit_codes

KINDS = {"cluster", "index"}


def build_argv(upstream: str, kind: str, python: Optional[str] = None) -> List[str]:
    py = python or "python"
    up = Path(upstream)
    if kind == "cluster":
        return [py, str(up / "cluster" / "train_cluster.py")]
    if kind == "index":
        return [py, str(up / "train_index.py"), "-c", "configs/config.json"]
    raise ValueError(f"unknown kind {kind}")


def expected_output(project: str, kind: str) -> Path:
    root = Path(project)
    if kind == "cluster":
        return root / "logs" / "44k" / "kmeans_10000.pt"
    return root / "logs" / "44k" / "feature_and_index.pkl"


def run_aux(
    project: str,
    upstream: str,
    kind: str,
    python: Optional[str] = None,
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    if kind not in KINDS:
        return {"kind": kind, "ok": False, "error": f"unsupported kind {kind}"}
    started = time.time()
    argv = build_argv(upstream, kind, python)
    proc = subprocess.run(
        argv,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = expected_output(project, kind)
    tail = (proc.stdout or "")[-4000:]
    result = {
        "kind": kind,
        "ok": proc.returncode == 0,
        "exitCode": proc.returncode,
        "elapsedSeconds": round(time.time() - started, 1),
        "output": str(output),
        "outputExists": output.is_file(),
        "stdoutTail": tail,
        "stderrTail": (proc.stderr or "")[-2000:],
    }
    # 脚本成功但产物缺失也算失败：没有产物就没法在推理里启用。
    if result["ok"] and not result["outputExists"]:
        result["ok"] = False
        result["error"] = "脚本执行成功但没有生成预期产物"
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge aux")
    parser.add_argument("--project", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    args = parser.parse_args(argv)

    result = run_aux(args.project, args.upstream, args.kind)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return exit_codes.OK if result["ok"] else exit_codes.TRAIN
