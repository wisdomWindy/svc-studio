"""错误码漂移检查：文档正文与 Rust/Python/TS 源码必须双向一致。

规则：
- 只比对 `## 3. 预留码` 之前的正文；预留码区块内的码视为"已规划未启用"，跳过。
- 退出码 0 = 一致；1 = 有漂移。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DOC = ROOT / "docs/requests/intent_770b99fe990a4a0d/error_codes.md"
SCAN_DIRS = [ROOT / "src-tauri/src", ROOT / "worker/svc_bridge", ROOT / "src"]
PATTERN = re.compile(r"SVC-\d{4}")
RESERVED_HEADING = "## 3. 预留码"


def active_doc_text() -> str:
    text = DOC.read_text(encoding="utf-8")
    index = text.find(RESERVED_HEADING)
    return text if index < 0 else text[:index]


def code_codes() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for base in SCAN_DIRS:
        for path in base.rglob("*"):
            if path.suffix not in {".rs", ".py", ".ts", ".tsx"} or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in PATTERN.findall(text):
                found.setdefault(match, []).append(str(path.relative_to(ROOT)))
    return found


def main() -> int:
    if not DOC.is_file():
        print(f"缺少文档：{DOC}")
        return 1

    documented = set(PATTERN.findall(active_doc_text()))
    implemented = code_codes()

    doc_only = sorted(documented - set(implemented))
    code_only = sorted(set(implemented) - documented)

    print(f"正文文档码 {len(documented)} 个；代码引用 {len(implemented)} 个")
    if doc_only:
        print("文档有、代码没有：")
        for code in doc_only:
            print(f"  - {code}")
    if code_only:
        print("代码有、文档没有：")
        for code in code_only:
            for path in sorted(set(implemented[code]))[:3]:
                print(f"  - {code} @ {path}")
    if not doc_only and not code_only:
        print("结果：一致，无漂移。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
