"""worker CLI 的输出契约：stdout 必须是一份可直接 json.loads 的 JSON。

背景：0.3.0 往 stdout 打了一行 `[separate] ...`，主程序把整份 stdout 当 JSON 解析，
直接报 `SVC-1001 worker 输出无法解析（expected value at line 1 column 2）`。
这组测试就是防止同类问题再发生——**任何会往 stdout 打日志的改动都会在这里失败**。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset_quality, dataset_separate

WORKER_ROOT = Path(__file__).resolve().parents[1]


def _make_wav(path: Path, seconds: float = 8.0) -> Path:
    """一段带静音间隙的测试音，方便切片逻辑正常走。"""
    import array
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * 44100)
    samples = array.array("h")
    for i in range(total):
        v = 0.4 * math.sin(2 * math.pi * 220 * i / 44100) if (i // 22050) % 2 == 0 else 0.0
        samples.append(int(v * 32767))
    audio.write_wav(path, samples, 44100, 1)
    return path


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(WORKER_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "svc_bridge", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        env=env,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["dataset", "scan"],
        ["dataset", "qc"],
        # 这条以前会往 stdout 打 `[separate] ...`：模型资产缺失时走三级回退，日志最多
        ["dataset", "separate"],
        ["dataset", "bandwidth"],
    ],
)
def test_stdout_is_single_json_object(work: Path, args: list[str]) -> None:
    raw = work / "dataset_raw"
    _make_wav(raw / "spk" / "song.wav")

    proc = _run_cli([*args, "--root", str(raw)], cwd=work)
    stdout = proc.stdout

    assert stdout.strip(), f"stdout 为空；stderr={proc.stderr[:500]}"
    assert len(stdout.strip().splitlines()) == 1, (
        "stdout 必须是单行 JSON，实际有多行（有日志串进了 stdout）：\n" + stdout[:800]
    )
    parsed = json.loads(stdout)  # 解析失败就是主程序看到的 SVC-1001
    assert isinstance(parsed, dict) and parsed.get("schema"), f"不是 worker 报告：{stdout[:200]}"


def test_separate_without_models_still_prints_json_only(work: Path) -> None:
    """没有分离模型时会走「RoFormer → MDX23C → Demucs」三级回退并打日志。

    这条路径 0.3.0 会把日志写进 stdout；这里断言即使回退日志出现，stdout 仍只有 JSON。
    """
    raw = work / "dataset_raw"
    _make_wav(raw / "spk" / "song.wav")
    proc = _run_cli(["dataset", "separate", "--root", str(raw)], cwd=work)
    parsed = json.loads(proc.stdout.strip())
    assert "separated" in parsed and "failed" in parsed, parsed


def test_separate_skips_products_and_slices(work: Path) -> None:
    """已分离的整段（x.vocals.wav）与切片（x.vocals_001.wav）都不能被再次分离。"""
    raw = work / "dataset_raw"
    spk = raw / "spk"
    _make_wav(spk / "song.vocals.wav")
    _make_wav(spk / "song.vocals_001.wav")

    report = dataset_separate.separate_tree(str(raw))

    assert report["ok"] is True
    assert report["separated"] == [], "已分离产物不应被当作原曲重跑"
    assert not (spk / "song.vocals.vocals.wav").exists()
    assert not (spk / "song.vocals_001.vocals.wav").exists()


def test_no_bare_stdout_prints_in_worker() -> None:
    """静态检查：除 doctor 的可读输出外，svc_bridge 下不允许裸 print（必须显式 file=）。"""
    offenders: list[str] = []
    for path in sorted((WORKER_ROOT / "svc_bridge").rglob("*.py")):
        if path.name == "doctor.py":  # 仅在非 --json 模式下打印人类可读结果
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if getattr(func, "id", None) != "print":
                continue
            if {kw.arg for kw in node.keywords} & {"file"}:
                continue
            offenders.append(f"{path.relative_to(WORKER_ROOT)}:{node.lineno}")
    assert not offenders, "这些 print 会把日志写进 stdout（主程序按 JSON 解析）：" + ", ".join(
        offenders
    )


def test_prune_quality_keep_flag_is_forwarded(work: Path) -> None:
    """`--keep 1.0` 必须真的生效（曾经被 __main__ 的参数重拼吞掉，退回 0.85 违规剔除文件）。"""
    raw = work / "dataset_raw"
    for i in range(10):
        _make_wav(raw / "spk" / f"f{i}.wav", seconds=3.0)

    keep_all = json.loads(
        _run_cli(["dataset", "prune-quality", "--root", str(raw), "--keep", "1.0"], cwd=work).stdout
    )
    assert keep_all["keepRatio"] == 1.0
    assert keep_all["droppedFiles"] == 0, "keep=1.0 不允许剔除任何文件"

    # 默认 0.85：10 个文件应剔除 1 个，证明参数确实被透传而不是被忽略
    default_keep = json.loads(
        _run_cli(["dataset", "prune-quality", "--root", str(raw)], cwd=work).stdout
    )
    assert default_keep["keepRatio"] == 0.85
    # round(10 * 0.15) = round(1.5) = 2（Python 的银行家舍入）
    assert default_keep["droppedFiles"] == 2
    assert dataset_quality  # 保持 import，确保模块可加载

def test_separate_events_mode_streams_progress_then_result(work: Path) -> None:
    """--events 模式：stdout 是 JSONL，进度单调推进，最后恰好一条 result。"""
    raw = work / "dataset_raw"
    spk = raw / "spk"
    for name in ("a.wav", "b.wav", "c.wav"):
        _make_wav(spk / name, seconds=6.0)

    proc = _run_cli(["dataset", "separate", "--root", str(raw), "--events"], cwd=work)
    events = []
    for line in proc.stdout.strip().splitlines():
        events.append(json.loads(line))  # 每行都必须是合法 JSON

    kinds = [e["kind"] for e in events]
    assert kinds.count("result") == 1, f"必须恰好一条 result，实际 {kinds}"
    assert kinds[-1] == "result"

    progress = [e for e in events if e["kind"] == "progress"]
    assert progress, "应至少上报一次进度"
    steps = [e["step"] for e in progress]
    assert steps[0] == 0, "开始时先报 0/总数"
    assert steps == sorted(steps), f"进度必须单调不减：{steps}"
    assert progress[-1]["total"] == 3
    assert steps[-1] in (3,), f"结束时步数应等于总数，实际 {steps[-1]}"
