"""G2 一致性检查：文档 12 类 ↔ 脚本用例 ↔ 最近一次证据。

三者必须对得上：
- error_codes.md 第 1 节表格有 12 行，且「用例」列以 PASS/SKIP 开头；
- error-cases.ps1 里每个 G2-1..G2-12 都有 Add-Row；
- evidence/G2/error-cases.json 有 12 条，Id 集合一致，Status 与文档一致。

输出只用 ASCII，避免 GBK 控制台下打印异常。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DOC = ROOT / "docs/requests/intent_770b99fe990a4a0d/error_codes.md"
SCRIPT = ROOT / "scripts/error-cases.ps1"
EVID = ROOT / "docs/requests/intent_770b99fe990a4a0d/evidence/G2/error-cases.json"


def doc_cases() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## 1.")
    end = text.index("## 2.")
    cases: dict[str, str] = {}
    for line in text[start:end].splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[0].isdigit():
            continue
        num = int(cells[0])
        usage = cells[5]
        status = "PASS" if usage.startswith("PASS") else ("SKIP" if usage.startswith("SKIP") else "?")
        cases[f"G2-{num}"] = status
    return cases


def script_ids() -> set[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    return {f"G2-{n}" for n in re.findall(r'Add-Row\s+"G2-(\d+)"', text)}


def evidence_rows() -> list[dict]:
    data = json.loads(EVID.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("rows", [])


def main() -> int:
    problems: list[str] = []

    doc = doc_cases()
    if len(doc) != 12:
        problems.append(f"doc rows: expected 12, got {len(doc)}")

    ids = script_ids()
    expected = {f"G2-{i}" for i in range(1, 13)}
    missing = sorted(expected - ids)
    extra = sorted(ids - expected)
    if missing:
        problems.append("script missing cases: " + ", ".join(missing))
    if extra:
        problems.append("script unexpected cases: " + ", ".join(extra))

    rows = evidence_rows()
    ev = {r["Id"]: r["Status"] for r in rows}
    if len(rows) != 12:
        problems.append(f"evidence rows: expected 12, got {len(rows)}")

    for case_id in sorted(expected):
        if case_id not in ev:
            problems.append(f"evidence missing {case_id}")
            continue
        if doc.get(case_id) not in ("PASS", "SKIP"):
            problems.append(f"doc {case_id} has no PASS/SKIP marker")
        elif doc[case_id] != ev[case_id]:
            problems.append(f"{case_id}: doc={doc[case_id]} evidence={ev[case_id]}")

    print(f"doc cases: {len(doc)}; script cases: {len(ids)}; evidence rows: {len(rows)}")
    if problems:
        for problem in problems:
            print("FAIL:", problem)
        return 1

    passed = sum(1 for v in ev.values() if v == "PASS")
    skipped = sum(1 for v in ev.values() if v == "SKIP")
    print(f"PASS={passed} SKIP={skipped} FAIL=0; doc/script/evidence consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
