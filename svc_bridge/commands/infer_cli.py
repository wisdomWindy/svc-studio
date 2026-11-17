"""`svc_bridge infer` 的命令行入口（独立文件，避免 `infer.py` 过长）。"""

from __future__ import annotations

import argparse
import json
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
