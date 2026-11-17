#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate request-workspace artifacts: JSON/YAML syntax + required files + link targets."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "intake.md",
    "scope.yaml",
    "compliance.md",
    "requirement-analysis.md",
    "PRD.md",
    "prd/appendix-a-parameters.md",
    "prd/appendix-b-error-codes.md",
    "prd/appendix-c-data-model.md",
    "requirement-splitting/requirements.md",
    "requirement-splitting/traceability.md",
    "design/page-design.md",
    "spec/spec.md",
    "design/architecture-design.md",
    "adr/ADR-001-ui-and-runtime.md",
    "adr/ADR-002-job-model.md",
    "adr/ADR-003-assets-offline.md",
    "state.json",
]

LINK_RE = re.compile(r"\]\(([^)#]+\.(?:md|yaml|json))\)")


def main() -> int:
    ok = True
    for rel in REQUIRED:
        p = ROOT / rel
        if p.exists() and p.stat().st_size > 0:
            print(f"FILE OK   {rel} ({p.stat().st_size} bytes)")
        else:
            ok = False
            print(f"FILE MISS {rel}")

    for p in sorted(ROOT.rglob("*.json")):
        try:
            json.loads(p.read_text(encoding="utf-8"))
            print(f"JSON OK   {p.relative_to(ROOT)}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"JSON FAIL {p.relative_to(ROOT)}: {e}")

    for p in sorted(ROOT.rglob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            print(f"YAML OK   {p.relative_to(ROOT)} keys={len(data)}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"YAML FAIL {p.relative_to(ROOT)}: {e}")

    broken = 0
    for md in sorted(ROOT.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "#")):
                continue
            resolved = (md.parent / target).resolve()
            if not resolved.exists():
                broken += 1
                ok = False
                print(f"LINK BREAK {md.relative_to(ROOT)} -> {target}")
    print(f"links broken={broken}")

    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
