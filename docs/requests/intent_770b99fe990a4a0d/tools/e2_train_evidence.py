"""E2 冒烟证据：真实 worker CLI + 假 train.py，验证 100 步停、事件流、smoke 配置。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "package.json").is_file())
OUT = ROOT / "docs/requests/intent_770b99fe990a4a0d/evidence/E2"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="e2-"))
    project = work / "proj"
    (project / "configs").mkdir(parents=True)
    (project / "configs/config.json").write_text(
        json.dumps({"train": {"batch_size": 2}}, ensure_ascii=False), encoding="utf-8"
    )
    (project / "logs/44k").mkdir(parents=True)

    upstream = work / "upstream"
    upstream.mkdir()
    shutil.copy(ROOT / "worker/tests/fixtures/fake_train.py", upstream / "train.py")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "worker")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "svc_bridge",
            "train",
            "--project",
            str(project),
            "--upstream",
            str(upstream),
            "--smoke",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        timeout=120,
    )

    events = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    progress = [e for e in events if e.get("kind") == "progress"]
    max_step = max((int(e.get("step") or 0) for e in progress), default=0)
    kinds = sorted({str(e.get("kind")) for e in events})

    report = {
        "exitCode": proc.returncode,
        "eventCount": len(events),
        "kinds": kinds,
        "progressEvents": len(progress),
        "maxStep": max_step,
        "smokeConfigCreated": (project / "reports/smoke_config.json").is_file(),
        "smokeConfig": json.loads((project / "reports/smoke_config.json").read_text(encoding="utf-8"))
        if (project / "reports/smoke_config.json").is_file()
        else None,
        "stderrTail": (proc.stderr or "")[-1200:],
    }
    (OUT / "train-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("exitCode", "eventCount", "kinds", "maxStep", "smokeConfigCreated")}, ensure_ascii=False))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
