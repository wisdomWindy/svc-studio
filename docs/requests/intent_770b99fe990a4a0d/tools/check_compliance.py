"""校验发布合规材料：SBOM 可解析、许可证齐全、源码包内容正确。

用法：python tools/check_compliance.py [artifacts/0.1.0]
退出码 0 = 通过；1 = 有不满足项（逐条打印原因）。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

REQUIRED_ENTRIES = [
    "src-tauri/src/lib.rs",
    "worker/svc_bridge/__main__.py",
    "package.json",
]
FORBIDDEN_PREFIXES = ["src-tauri/target/", "node_modules/", "dist/"]


def check(out: Path) -> list[str]:
    problems: list[str] = []

    sbom_path = out / "SBOM.json"
    if not sbom_path.is_file():
        problems.append("缺少 SBOM.json")
    else:
        raw = sbom_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append("SBOM.json 带 UTF-8 BOM，标准解析器会报错")
        try:
            sbom = json.loads(raw.decode("utf-8-sig"))
        except json.JSONDecodeError as exc:
            problems.append(f"SBOM.json 无法解析：{exc}")
            sbom = None
        if sbom is not None:
            if sbom.get("bomFormat") != "CycloneDX":
                problems.append("SBOM 不是 CycloneDX 格式")
            names = {c.get("name") for c in sbom.get("components", [])}
            if "so-vits-svc" not in names:
                problems.append("SBOM 缺少上游 so-vits-svc 组件")

    licenses = out / "licenses"
    agpl = licenses / "AGPL-3.0.txt"
    offer = licenses / "SOURCE-OFFER.txt"
    if not agpl.is_file():
        problems.append("缺少 licenses/AGPL-3.0.txt")
    elif "GNU AFFERO GENERAL PUBLIC LICENSE" not in agpl.read_text(encoding="utf-8", errors="replace"):
        problems.append("AGPL 全文内容不完整")
    if not offer.is_file():
        problems.append("缺少 licenses/SOURCE-OFFER.txt")

    zips = list(out.glob("source-*.zip"))
    if not zips:
        problems.append("缺少源码归档 source-*.zip")
    else:
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            for required in REQUIRED_ENTRIES:
                if not any(n.endswith(required) for n in names):
                    problems.append(f"源码包缺少 {required}")
            leaked = [n for n in names if any(n.startswith(p) for p in FORBIDDEN_PREFIXES)]
            if leaked:
                problems.append(f"源码包混入构建产物：{leaked[:3]}")

    return problems


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/0.1.0")
    if not out.is_dir():
        print(f"FAIL: 产物目录不存在：{out}")
        return 1
    problems = check(out)
    if problems:
        print(f"FAIL: {len(problems)} 项不满足")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"PASS: 合规材料齐全且可解析（{out}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
