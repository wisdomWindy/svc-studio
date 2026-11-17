"""OOM 自愈：第一次显存不足 → 自动把 batch_size 减半 → 重试一次成功。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from svc_bridge.commands import train

FAKE = '''\
import json, sys
from pathlib import Path

args = sys.argv
cfg = Path(args[args.index("-c") + 1])
marker = cfg.parent.parent / "attempt.txt"
n = int(marker.read_text()) if marker.exists() else 0
marker.write_text(str(n + 1))

if n == 0:
    print("RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB")
    sys.exit(1)

batch = json.loads(cfg.read_text())["train"]["batch_size"]
print(f"# batch_size={batch}", flush=True)
for step in range(1, 6):
    losses = f"[{1.0 / step:.4f}, 0.5]"
    print(f"Losses: {losses}, step: {step}, lr: 0.0001", flush=True)
'''


@pytest.fixture()
def project():
    import shutil
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="svcoom-"))
    (path / "configs").mkdir(parents=True)
    (path / "reports").mkdir(parents=True)
    (path / "configs" / "config.json").write_text(
        json.dumps(
            {
                "train": {"eval_interval": 800, "log_interval": 200, "keep_ckpts": 3, "batch_size": 8},
                "model": {},
            }
        ),
        encoding="utf-8",
    )
    (path / "fake_train.py").write_text(FAKE, encoding="utf-8")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_oom_triggers_one_retry_with_halved_batch(project: Path) -> None:
    events = []
    data = train.run_train(
        str(project),
        str(project),
        "configs/config.json",
        "44k",
        max_steps=5,
        train_script=str(project / "fake_train.py"),
        emit=events.append,
    )

    assert data["oomDetected"] is True, "第一次必须被识别为 OOM"
    assert data["retried"] is True, "OOM 后应自动重试一次"
    assert data["batchSizeAfterRetry"] == 4
    assert data["lastStep"] == 5, "重试后应正常训练到上限"
    assert data["exitCode"] == 0

    saved = json.loads((project / "configs" / "config.json").read_text(encoding="utf-8"))
    assert saved["train"]["batch_size"] == 4, "配置里的 batch_size 必须真的被改小"

    warns = [e for e in events if e.get("kind") == "log" and e.get("level") == "warn"]
    assert any("batch_size" in w["message"] for w in warns)
    results = [e for e in events if e.get("kind") == "result"]
    assert len(results) == 1, "重试时不应重复发 result 事件"


def test_oom_without_batch_field_does_not_loop(project: Path) -> None:
    cfg = project / "configs" / "config.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    del doc["train"]["batch_size"]
    cfg.write_text(json.dumps(doc), encoding="utf-8")

    data = train.run_train(
        str(project),
        str(project),
        "configs/config.json",
        "44k",
        max_steps=5,
        train_script=str(project / "fake_train.py"),
        emit=lambda _e: None,
    )
    assert data["oom"] is True
    assert data.get("retried") is not True, "没有可降的字段时不应假装重试"
