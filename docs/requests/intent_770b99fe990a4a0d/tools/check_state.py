"""核对 state.json 与实际文件系统：artifacts 声明存在、必需交付物齐全、状态与磁盘一致。

用法：python docs/requests/intent_770b99fe990a4a0d/tools/check_state.py
退出码：0 一致；1 有缺失或状态陈旧。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"

REQUIRED = [
    "intake.md",
    "scope.yaml",
    "compliance.md",
    "requirement-splitting/requirements.md",
    "requirement-splitting/traceability.md",
    "design/page-design.md",
    "spec/spec.md",
    "design/architecture-design.md",
    "plan/plan.md",
    "plan/task-board.md",
    "execution/changelog.md",
    "verification/verification.md",
    "review/review.md",
    "error_codes.md",
    "spike/report.md",
    "adr/ADR-001-ui-and-runtime.md",
    "adr/ADR-002-job-model.md",
    "adr/ADR-003-assets-offline.md",
    "tools/check_traceability.py",
    "tools/check_verification.py",
    "tools/check_error_codes.py",
    "tools/check_error_cases.py",
    "tools/check_compliance.py",
]

# 这些条目允许保持 pending，但必须写清原因（当前为 0 条）
ALLOWED_PENDING: dict[str, str] = {}


def main() -> int:
    data = json.loads(STATE.read_text(encoding="utf-8"))
    problems: list[str] = []

    artifacts = data.get("artifacts", {})
    for key, status in artifacts.items():
        target = ROOT / key
        exists = target.exists()
        pending = "pending" in str(status).lower()
        if not exists and not pending:
            problems.append(f"artifacts 声明 {key}={status!r}，但文件不存在")
        if pending and key not in ALLOWED_PENDING:
            problems.append(f"artifacts {key} 仍为 pending，但未登记允许原因")
        if exists and pending:
            problems.append(f"artifacts {key} 标为 pending，但文件已存在——状态陈旧")

    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            problems.append(f"缺少必需交付物：{rel}")

    approvals = data.get("approvals", {})
    for gate in ("spec_approved", "plan_approved"):
        if not approvals.get(gate):
            problems.append(f"{gate} 未批准")

    stage = data.get("stage")
    if stage not in {"execute", "verify", "review", "complete"}:
        problems.append(f"stage={stage} 与已完成的 WP 不符")

    if problems:
        print(f"FAIL：{len(problems)} 个问题")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"OK：artifacts {len(artifacts)} 项、必需交付物 {len(REQUIRED)} 项、stage={stage}，与磁盘一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
