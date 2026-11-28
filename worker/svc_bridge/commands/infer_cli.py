"""`svc_bridge infer` 的命令行入口（独立文件，避免 `infer.py` 过长）。

进度上报分两层：
- **文件内**：后台线程按预计耗时推进（分离+转换），封顶 95%，避免单文件长时间 0% 不动；
  上界受限为「当前文件边界」，多文件时不会越过还没开始的文件。
- **文件间**：每完成一个文件用真实 `已完成/总数` 覆盖一次。
两层都用百分比刻度（step/total = 0..100），保证主进程的 ratio 单调且可比。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from .. import exit_codes
from ..rpc import emit, emit_log
from . import infer, vocal_mix

# 实测（RTX 5060 Ti，RoFormer + so-vits-svc 4.8.1）：
# 分离约 0.6×音频时长，转换约 0.2×音频时长。两阶段分开估时，
# 时间进度只用于"别让界面卡住"，真实完成数一到就覆盖成确切值。
SEP_SECONDS_PER_AUDIO_SECOND = 0.60
CONV_SECONDS_PER_AUDIO_SECOND = 0.20


def _total_seconds(inputs: List[Path]) -> float:
    total = 0.0
    for p in inputs:
        try:
            total += infer._seconds_of(p)
        except Exception:  # noqa: BLE001
            total += 180.0  # 读不出时长就按 3 分钟估
    return total


class _ProgressTicker:
    """文件内进度：分离 0–55%、转换 55–100%，按阶段实测速率推进。

    约束：
    - 单调不回退
    - 单文件内容期封顶 95%（结尾由真实完成数覆盖成 100）
    - 多文件时限制在「当前文件窗口」内，不越过还没开始的文件
    """

    SEP_WEIGHT = 0.55

    def __init__(self, total_inputs: int, audio_seconds: float, separate: bool) -> None:
        self._total_inputs = max(total_inputs, 1)
        self._audio = max(audio_seconds, 1.0)
        self._separate = separate
        self._done = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last = 0
        self._phase = "sep" if separate else "conv"
        self._phase_started = time.time()
        self._in_file = 0.0  # 当前文件内部进度 0..1

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enter_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self._phase_started = time.time()

    def _phase_progress(self) -> float:
        elapsed = time.time() - self._phase_started
        if self._phase == "sep":
            est = self._audio * SEP_SECONDS_PER_AUDIO_SECOND
            frac = min(1.0, elapsed / max(est, 1.0)) * self.SEP_WEIGHT
        else:
            est = self._audio * CONV_SECONDS_PER_AUDIO_SECOND
            frac = self.SEP_WEIGHT + min(1.0, elapsed / max(est, 1.0)) * (1 - self.SEP_WEIGHT)
        return max(self._in_file, min(1.0, frac))

    def _emit(self, percent: int, cap: int = 95) -> None:
        percent = max(self._last, min(cap, percent))
        if percent == self._last:
            return
        self._last = percent
        emit("progress", stage="inference", step=percent, total=100, status="running")

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            window_lo = self._done / self._total_inputs * 100
            window_hi = (self._done + 0.95) / self._total_inputs * 100
            self._emit(int(window_lo + (window_hi - window_lo) * self._phase_progress()))

    def item_done(self, done: int, total: int) -> None:
        self._done = done
        self._in_file = 0.0
        self._phase = "sep" if self._separate else "conv"
        self._phase_started = time.time()
        # 真实完成数不受 95% 封顶限制：全部完成就该是 100
        self._emit(int(done / max(total, 1) * 100), cap=100)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def _instrument_separation(ticker: "_ProgressTicker") -> None:
    """把分离调用包一层，让界面知道现在卡在「分离人声」还是「转换」。"""
    original = vocal_mix.separate_song
    if getattr(original, "_svcstudio_instrumented", False):
        return

    def wrapped(*args, **kwargs):
        ticker.enter_phase("sep")
        emit("stage", stage="separate", message="分离人声…")
        result = original(*args, **kwargs)
        engine = (result or {}).get("engine") or "separator"
        ticker.enter_phase("conv")
        emit("stage", stage="convert", message=f"分离完成（{engine}），开始转换…")
        return result

    wrapped._svcstudio_instrumented = True  # type: ignore[attr-defined]
    vocal_mix.separate_song = wrapped  # type: ignore[assignment]
    infer.vocal_mix.separate_song = wrapped  # type: ignore[assignment]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="svc_bridge infer")
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--params", required=True, help="参数 JSON 文件")
    parser.add_argument("--spk", required=True)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--batch-name", default="batch")
    args = parser.parse_args(argv)

    params = json.loads(Path(args.params).read_text(encoding="utf-8-sig"))
    inputs = [Path(p) for p in args.inputs]

    # 上游 `Svc` 用相对路径找 pretrain/ 与 logs/，必须把工作目录切到项目根。
    project_dir = Path(args.project).resolve()
    upstream = os.environ.get("SVCSTUDIO_UPSTREAM_DIR", "").strip()
    if upstream and upstream not in sys.path:
        sys.path.insert(0, upstream)
    previous_cwd = os.getcwd()
    os.chdir(project_dir)

    # 关闭分离时没有「分离」阶段，进度直接按转换阶段估时
    ticker = _ProgressTicker(
        len(inputs), _total_seconds(inputs), separate=bool(params.get("separateVocals", True))
    )
    _instrument_separation(ticker)
    emit("stage", stage="inference", message="开始推理：%d 条" % len(inputs))
    ticker.start()
    try:
        report = infer.run_batch(
            Path(args.project),
            args.model,
            args.config,
            params,
            args.spk,
            inputs,
            batch_name=str(args.batch_name),
            on_item=lambda done, total, item: (
                ticker.item_done(done, total),
                emit("stage", stage="inference", message=f"已完成 {done}/{total}"),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        emit("error", code="SVC-6001", message="推理失败：%s" % exc, retriable=False)
        return exit_codes.INFER
    finally:
        ticker.stop()
        os.chdir(previous_cwd)

    spk_note = report.get("spkNote") or ""
    if spk_note:
        emit_log(spk_note, level="warn")
    issues = [i for i in report.get("items", []) if i.get("separateError")]
    if issues:
        emit_log(f"人声分离失败，已回退整段转换：{issues[0]['separateError']}", level="warn")
    summary = report["summary"]
    emit_log("批次完成：ok=%s failed=%s" % (summary["ok"], summary["failed"]))
    emit(
        "result",
        data={
            "reportPath": str(Path(report["batch"]) / "report.json"),
            "batch": report["batch"],
            "summary": summary,
        },
    )
    return exit_codes.OK if summary["ok"] else exit_codes.INFER
