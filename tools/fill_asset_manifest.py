"""把真实下载的权重写进 assets/manifest.json，并做一次完整性核对。

用途：W3-5/B3 的"离线导入"需要清单里带真实 sha256；空 files[] 等于没有清单。
本脚本只读本地文件、不改动权重，用于生成本地开发清单（发布清单需签名）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(os.environ["USERPROFILE"]) / "SVCStudio" / "pretrain"
MANIFEST = Path("assets/manifest.json")

# 清单 id -> [(相对数据根的路径)]
WANTED = {
    "contentvec": ["pretrain/checkpoint_best_legacy_500.pt"],
    "rmvpe": ["pretrain/rmvpe.pt"],
    "nsf_hifigan": ["pretrain/nsf_hifigan/model", "pretrain/nsf_hifigan/config.json"],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    data_root = ROOT.parent
    filled = 0
    missing = []

    for asset in manifest["assets"]:
        rels = WANTED.get(asset["id"])
        if not rels:
            continue
        files = []
        for rel in rels:
            path = data_root / rel
            if not path.is_file():
                missing.append(rel)
                continue
            files.append(
                {
                    "relPath": rel,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        if files:
            asset["files"] = files
            filled += len(files)

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    # 回读核对：清单里的 sha256 必须与磁盘一致
    check = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mismatched = []
    for asset in check["assets"]:
        for f in asset["files"]:
            path = data_root / f["relPath"]
            if not path.is_file() or sha256(path) != f["sha256"]:
                mismatched.append(f["relPath"])

    evidence = {
        "generatedAt": __import__("datetime").datetime.now().astimezone().isoformat(),
        "filledFiles": filled,
        "missing": missing,
        "mismatched": mismatched,
        "assets": [
            {"id": a["id"], "files": len(a["files"]), "bytes": sum(f["bytes"] for f in a["files"])}
            for a in check["assets"]
            if a["files"]
        ],
    }
    out = Path("docs/requests/intent_770b99fe990a4a0d/evidence/B3/manifest-fill.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if (not mismatched and not missing) else 1


if __name__ == "__main__":
    raise SystemExit(main())
