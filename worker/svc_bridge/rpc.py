"""JSON Lines 协议：事件上行、控制下行。

约束：
- stdout 只写协议行，每条一行、UTF-8、`\\n` 结尾、立即 flush；
- 单写者：所有事件都经 `emit`，内部加锁，避免多线程交错；
- 行内不含换行，`json.dumps(ensure_ascii=False)` 后不会产生裸换行。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Dict, Optional, TextIO

PROTOCOL_VERSION = 1
_lock = threading.Lock()
_out: Optional[TextIO] = None


def _writer() -> TextIO:
    """返回当前 stdout；只在流对象变化时重配（测试会替换 sys.stdout）。"""
    global _out
    current = sys.stdout
    if _out is not current:
        _out = current
        try:  # 行缓冲，保证 Rust 侧逐行可读
            _out.reconfigure(encoding="utf-8", newline="\n", line_buffering=True)  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    return _out


def emit(kind: str, **fields: Any) -> None:
    """写一条事件。kind ∈ stage|progress|log|result|error|hello。"""
    payload: Dict[str, Any] = {"v": PROTOCOL_VERSION, "kind": kind}
    payload.update(fields)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _lock:
        stream = _writer()
        stream.write(line + "\n")
        stream.flush()


def emit_log(message: str, level: str = "info") -> None:
    emit("log", level=level, message=message)


def emit_progress(stage: str, step: int, total: int) -> None:
    emit("progress", stage=stage, step=step, total=total)


def emit_error(code: str, message: str, cause: str = "", retriable: bool = False) -> None:
    emit(
        "error",
        code=code,
        message=message,
        cause=cause,
        retriable=retriable,
    )


def read_control() -> Optional[Dict[str, Any]]:
    """从 stdin 读一条控制指令；EOF 返回 None。"""
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {"cmd": "invalid"}
    return data if isinstance(data, dict) else {"cmd": "invalid"}


def redirect_stdout_to_stderr() -> None:
    """第三方库（torch/fairseq/tqdm）会往 stdout 打印，必须在导入前改道。"""
    sys.stdout = sys.stderr  # noqa: A001 - 协议要求


def is_cancelled() -> bool:
    """非阻塞检查取消标记（由外层轮询 stdin 的线程写入）。"""
    return os.environ.get("SVCSTUDIO_CANCEL") == "1"
