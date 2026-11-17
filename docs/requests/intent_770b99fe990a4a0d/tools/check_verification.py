"""验收台账自检：证据文件必须存在，汇总数量必须与表格一致。

用法：python docs/requests/intent_770b99fe990a4a0d/tools/check_verification.py
退出码：0 一致；1 有缺失或数量不符。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "verification" / "verification.md"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 老环境下退化为 ASCII 输出
        pass
    text = DOC.read_text(encoding="utf-8")

    # 1) 证据路径存在性
    refs = sorted(set(re.findall(r"evidence/[A-Za-z0-9_./\-\u4e00-\u9fff ]+?\.(?:png|json|md)", text)))
    missing = [r for r in refs if not (ROOT / r.strip()).is_file()]

    # 2) 表格状态计数（只看 A–I 章节的 "| X |" 行）
    rows = [
        line
        for line in text.splitlines()
        if re.match(r"^\|\s*[A-I]\d?\s+", line)
    ]
    ok = sum(1 for r in rows if "✅" in r)
    partial = sum(1 for r in rows if "🟡" in r)
    blocked = sum(1 for r in rows if "⛔" in r)

    # 3) 汇总声明
    declared = {}
    for key, label in (("ok", "有证据"), ("partial", "部分"), ("blocked", "阻塞")):
        m = re.search(rf"\|\s*(?:✅|🟡|⛔)\s*{label}\s*\|\s*(\d+)\s*\|", text)
        declared[key] = int(m.group(1)) if m else -1

    problems = []
    if missing:
        problems.append("证据文件缺失：" + "、".join(missing))
    actual = {"ok": ok, "partial": partial, "blocked": blocked}
    for key in actual:
        if declared[key] != actual[key]:
            problems.append(
                f"汇总数量不符：{key} 声明 {declared[key]}，实际 {actual[key]}"
            )

    print(f"证据引用 {len(refs)} 个，缺失 {len(missing)} 个")
    print(f"表格行 {len(rows)} 条：OK={ok} / PARTIAL={partial} / BLOCKED={blocked}")
    if problems:
        for p in problems:
            print("FAIL " + p)
        return 1
    print("RESULT PASS：证据可达，汇总与表格一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
