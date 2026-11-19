"""`svc_bridge infer` 的命令行入口（独立文件，避免 `infer.py` 过长）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .. import exit_codes
from ..rpc import emit, emit_log
from . import infer


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="svc_bridge infer")
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--params", required=True, help="参数 JSON 文件")
    parser.add_argument("--spk", required=True)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--batch-name", default="batch")
    args = parser.parse_args(argv)

    params = json.loads(Path(args.params).read_text(encoding="utf-8-sig"))
    inputs = [Path(p) for p in args.inputs]

    # 上游 `Svc` 用相对路径找 pretrain/ 与 logs/，必须把工作目录切到项目根。
    project_dir = Path(args.project).resolve()
    upstream = os.environ.get("SVCSTUDIO_UPSTREAM_DIR", "").strip()
    if upstream and upstream not in sys.path:
        sys.path.insert(0, upstream)
    previous_cwd = os.getcwd()
    os.chdir(project_dir)
    emit("stage", stage="inference", message="开始推理：%d 条" % len(inputs))

    try:
        report = infer.run_batch(
            Path(args.project),
            args.model,
            args.config,
            params,
            args.spk,
            inputs,
            batch_name=str(args.batch_name),
            on_item=lambda done, total, item: emit(
                "progress", stage="inference", step=done, total=total, status=item["status"]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        emit("error", code="SVC-6001", message="推理失败：%s" % exc, retriable=False)
        return exit_codes.INFER
    finally:
        os.chdir(previous_cwd)

    spk_note = report.get("spkNote") or ""
    if spk_note:
        emit_log(spk_note, level="warn")
    summary = report["summary"]
    emit_log("批次完成：ok=%s failed=%s" % (summary["ok"], summary["failed"]))
    emit(
        "result",
        data={
            "reportPath": str(Path(report["batch"]) / "report.json"),
            "batch": report["batch"],
            "summary": summary,
        },
    )
    return exit_codes.OK if summary["ok"] else exit_codes.INFER
