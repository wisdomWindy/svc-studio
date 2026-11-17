#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate + validate the requirement <-> acceptance traceability matrix.

Parses requirement-splitting/requirements.md, cross-checks against the canonical
acceptance ID set (from the workbench acceptance checklist), and rewrites
requirement-splitting/traceability.md.

Exit code 0 only when:
  * every acceptance ID is owned by exactly one requirement
  * every requirement owns at least one acceptance ID
  * every acceptance ID in the canonical set exists in the matrix
  * P0 requirement count == 12
  * every blocking acceptance ID is owned by a P0 requirement
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ_MD = ROOT / "requirement-splitting" / "requirements.md"
OUT_MD = ROOT / "requirement-splitting" / "traceability.md"

ACCEPTANCE = {
    "A1": "范围锁定", "A2": "使用规约确认", "A3": "AGPL 合规",
    "B1": "doctor 九项", "B2": "修复指引", "B3": "离线导入", "B4": "篡改检测", "B5": "诊断包",
    "C1": "结构校验", "C2": "QC 指标", "C3": "切片", "C4": "采样率与声道",
    "C5": "阈值断言", "C6": "低数据量豁免",
    "D1": "全链执行", "D2": "计数一致", "D3": "配置校验", "D4": "前置断言", "D5": "断点幂等",
    "E1": "参数推荐", "E2": "100 步冒烟", "E3": "长训稳定", "E4": "断点续训",
    "E5": "取消与释放", "E6": "OOM 自愈", "E7": "扩散与聚类", "E8": "模型登记",
    "F1": "单条推理", "F2": "削波与峰值", "F3": "性能", "F4": "参数面",
    "F5": "歌声保护", "F6": "whisper-ppg 适配", "F7": "批量隔离", "F8": "可复现",
    "G1": "主线程响应", "G2": "错误可读性", "G3": "崩溃隔离", "G4": "关闭不误杀",
    "G5": "24h 浸泡", "G6": "路径健壮性",
    "H1": "干净机安装", "H2": "离线全流程", "H3": "DLL 完整性", "H4": "卸载干净",
    "H5": "体积与启动", "H6": "回滚",
    "I1": "矩阵覆盖", "I2": "结论文件",
}
BLOCKING = {"A1", "A3", "B1", "C5", "E2", "E5", "F1", "H1", "H2", "H4"}

HEAD_RE = re.compile(r"\*\*(REQ-\d{3})\s+([^*）]+)\*\*（(P[012])")
ACC_RE = re.compile(r"验收：([A-I]\d(?:、[A-I]\d)*)。")


def parse_requirements() -> dict[str, dict]:
    text = REQ_MD.read_text(encoding="utf-8")
    blocks = HEAD_RE.split(text)
    reqs: dict[str, dict] = {}
    # split -> [preamble, id, name, priority, body, id, name, priority, body, ...]
    for i in range(1, len(blocks), 4):
        rid, name, prio, body = blocks[i], blocks[i + 1].strip(), blocks[i + 2], blocks[i + 3]
        found = ACC_RE.search(body)
        if not found:
            raise SystemExit(f"{rid} 缺少『验收：…』行")
        acc = [x.strip() for x in found.group(1).split("、")]
        reqs[rid] = {"name": name, "priority": prio, "acceptance": acc}
    return reqs


def main() -> int:
    reqs = parse_requirements()
    errors: list[str] = []

    owner: dict[str, list[str]] = {}
    for rid, r in sorted(reqs.items()):
        if not r["acceptance"]:
            errors.append(f"{rid} 没有任何验收项")
        for a in r["acceptance"]:
            if a not in ACCEPTANCE:
                errors.append(f"{rid} 引用了未登记的验收项 {a}")
            owner.setdefault(a, []).append(rid)

    for a in ACCEPTANCE:
        if a not in owner:
            errors.append(f"验收项 {a} 没有需求承接（孤儿验收）")
        elif len(owner[a]) > 1:
            errors.append(f"验收项 {a} 被多个需求重复承接: {owner[a]}")

    p0 = [rid for rid, r in reqs.items() if r["priority"] == "P0"]
    if len(p0) != 12:
        errors.append(f"P0 需求数量为 {len(p0)}，要求恰好 12")

    p0_owned = {a for rid in p0 for a in reqs[rid]["acceptance"]}
    lost = BLOCKING - p0_owned
    if lost:
        errors.append(f"阻断验收项未被 P0 需求承接: {sorted(lost)}")

    lines: list[str] = []
    lines.append("# 追溯矩阵：需求 ↔ 验收（自动生成，请勿手改）")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')}")
    lines.append(f"- 生成脚本：`tools/check_traceability.py`（解析 `requirements.md` 的 `REQ-xxx` 与『验收：…』行）")
    lines.append(f"- 需求数：{len(reqs)}（P0={len(p0)}，P1={len(reqs) - len(p0)}，P2=0，P2 清单见 requirements.md §4）")
    lines.append(f"- 验收项：{len(ACCEPTANCE)}（阻断 {len(BLOCKING)}）")
    lines.append(f"- 覆盖率：{len(owner)}/{len(ACCEPTANCE)} = {len(owner) / len(ACCEPTANCE):.0%}")
    lines.append(f"- 校验结果：{'PASS' if not errors else 'FAIL'}")
    lines.append("")
    if errors:
        lines.append("## 校验失败明细")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("## 1. 需求 → 验收")
    lines.append("")
    lines.append("| 需求 | 优先级 | 名称 | 验收项 | 含阻断项 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for rid, r in sorted(reqs.items()):
        acc = "、".join(r["acceptance"])
        hit = "是" if any(a in BLOCKING for a in r["acceptance"]) else "—"
        lines.append(f"| {rid} | {r['priority']} | {r['name']} | {acc} | {hit} |")

    lines.append("")
    lines.append("## 2. 验收 → 需求")
    lines.append("")
    lines.append("| 验收 | 要点 | 需求 | 需求优先级 | 阻断 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for a in ACCEPTANCE:
        rid = owner.get(a, ["—"])[0]
        prio = reqs[rid]["priority"] if rid in reqs else "—"
        lines.append(
            f"| {a} | {ACCEPTANCE[a]} | {rid} | {prio} | {'是' if a in BLOCKING else '—'} |"
        )

    lines.append("")
    lines.append("## 3. 门禁判定（G1）")
    lines.append("")
    lines.append("| 判据 | 结果 |")
    lines.append("| --- | --- |")
    lines.append(f"| 100% 需求有验收项 | {'通过' if not any(not r['acceptance'] for r in reqs.values()) else '失败'} |")
    lines.append(f"| 矩阵无孤儿、无重复 | {'通过' if not any(len(owner.get(a, [])) != 1 for a in ACCEPTANCE) else '失败'} |")
    lines.append(f"| P0 ≤ 12 且恰好 12 | {'通过' if len(p0) == 12 else '失败'} |")
    lines.append(f"| 阻断项全部由 P0 承接 | {'通过' if not lost else '失败'} |")
    lines.append("")
    lines.append("### 脚本输出")
    lines.append("")
    lines.append("```text")
    lines.append(f"requirements={len(reqs)} p0={len(p0)} acceptance={len(ACCEPTANCE)} covered={len(owner)}")
    if errors:
        for e in errors:
            lines.append(f"ERROR {e}")
        lines.append("RESULT FAIL")
    else:
        lines.append("orphan=0 duplicate=0 missing=0")
        lines.append("RESULT PASS")
    lines.append("```")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"requirements={len(reqs)} p0={len(p0)} acceptance={len(ACCEPTANCE)} covered={len(owner)}")
    for e in errors:
        print(f"ERROR {e}")
    print("RESULT", "FAIL" if errors else "PASS")
    print(f"written: {OUT_MD}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
