"""训练编排：命令构造、冒烟配置、日志解析与 100 步优雅停止。

冒烟不伪造训练：真的启动 `train.py`，只是把 `eval_interval` 降到 50 保证 100 步内落盘，
并在读到第 100 步后终止整棵进程树（Windows 上 train.py 会派生 DataLoader 子进程）。
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import exit_codes

Emitter = Callable[[Dict[str, Any]], None]

STEP_RE = re.compile(r"Losses:\s*\[([^\]]*)\],\s*step:\s*(\d+)")
EPOCH_RE = re.compile(r"Train Epoch:\s*(\d+)")
OOM_HINTS = ("out of memory", "outofmemory")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def build_smoke_config(
    src: str, dst: str, eval_interval: int = 50, log_interval: int = 10
) -> Dict[str, Any]:
    """复制正式配置并覆盖训练节奏；不修改原文件。"""
    doc = json.loads(Path(src).read_text(encoding="utf-8-sig"))
    train = doc.setdefault("train", {})
    train["eval_interval"] = eval_interval
    train["log_interval"] = log_interval
    train["keep_ckpts"] = 2
    target = Path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return doc



def force_single_process_loader(config_path: str) -> None:
    """4.8.1 的 DataLoader 默认多进程；在 Windows 上由父进程 spawn 常崩，统一改成 0。"""
    try:
        path = Path(config_path)
        if not path.is_file():
            return
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        data = doc.setdefault("data", {})
        if data.get("n_workers") != 0:
            data["n_workers"] = 0
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

def build_argv(
    upstream: str,
    config_path: str,
    model_dir: str,
    python: Optional[str] = None,
    kind: str = "main",
) -> List[str]:
    py = python or "python"
    if kind == "diffusion":
        return [py, str(Path(upstream) / "train_diff.py"), "-c", config_path]
    return [py, str(Path(upstream) / "train.py"), "-c", config_path, "-m", model_dir]


def _kill_tree(proc: "subprocess.Popen[str]") -> None:
    """终止训练进程及其派生进程。Windows 用 taskkill /T，其它平台直接 kill。"""
    if platform.system() == "Windows":
        from ..procwin import no_window_kwargs

        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            **no_window_kwargs(),
        )
        return
    proc.kill()


def _parse_step(line: str) -> Optional[Dict[str, Any]]:
    match = STEP_RE.search(line)
    if not match:
        return None
    raw = match.group(1)
    losses: List[float] = []
    for token in raw.split(","):
        token = token.strip().replace("tensor(", "").rstrip(")")
        try:
            losses.append(float(token))
        except ValueError:
            return None
    if any(v != v or v in (float("inf"), float("-inf")) for v in losses):
        return {"diverged": True, "step": int(match.group(2)), "losses": losses}
    lr_match = re.search(r"lr:\s*([0-9.eE+-]+)", line)
    return {
        "step": int(match.group(2)),
        "losses": losses,
        "lr": float(lr_match.group(1)) if lr_match else None,
    }



def classify_oom(text: str) -> str:
    """区分两类“out of memory”：

    - cuda：显存不足（torch.cuda.OutOfMemoryError / CUDA out of memory）
    - host：主机内存不足（MemoryError / 无法分配）

    只有 cuda 才值得把 batch_size 减半重试；主机内存不足减 batch 没用。
    """
    lowered = text.lower()
    if "cuda out of memory" in lowered or "cudaerroroutofmemory" in lowered:
        return "cuda"
    if "outofmemoryerror" in lowered and "cuda" in lowered:
        return "cuda"
    if "memoryerror" in lowered or "out of memory" in lowered or "outofmemory" in lowered:
        return "host"
    return ""

def halve_batch_size(config_path: str, minimum: int = 1) -> Optional[int]:
    """OOM 自愈：把 train.batch_size 减半并原子写回；没有该字段就返回 None。"""
    path = Path(config_path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    train = doc.get("train")
    if not isinstance(train, dict):
        return None
    current = train.get("batch_size")
    if not isinstance(current, int) or current <= minimum:
        return None
    train["batch_size"] = max(minimum, current // 2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return int(train["batch_size"])

def run_train(
    project: str,
    upstream: str,
    config_path: str,
    model_dir: str,
    max_steps: Optional[int] = None,
    kind: str = "main",
    emit: Optional[Emitter] = None,
    train_script: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    retry_oom: bool = True,
    retry_crash: bool = True,
    _emit_result: bool = True,
) -> Dict[str, Any]:
    project_path = Path(project)
    _cfg = Path(config_path)
    if not _cfg.is_absolute():
        _cfg = project_path / config_path
    force_single_process_loader(str(_cfg))
    argv = build_argv(upstream, config_path, model_dir, kind=kind)
    if train_script:
        argv[1] = train_script
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(upstream)),
        "SVCSTUDIO_UPSTREAM_DIR": str(Path(upstream)),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    def send(event: Dict[str, Any]) -> None:
        if emit is not None:
            emit(event)

    send({"kind": "stage", "stage": kind, "message": f"启动训练：{' '.join(argv)}"})
    started = time.time()
    from ..procwin import no_window_kwargs

    proc = subprocess.Popen(
        argv,
        cwd=str(project_path),
        **no_window_kwargs(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    last_step = 0
    oom = False
    host_oom = False
    diverged = False
    saw_traceback = False
    # 训练日志落盘：驱动重置/崩溃时事件里可能什么都没有，文件是唯一线索
    log_dir = Path(project) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stderr_log = log_dir / f"train-{time.strftime('%Y%m%d-%H%M%S')}.stderr.log"
    log_file = open(stderr_log, "a", encoding="utf-8", errors="replace")
    stopped_for_smoke = False
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_file.write(line + "\n")
            log_file.flush()
            if "Traceback (most recent call last)" in line:
                saw_traceback = True
            lowered = line.lower()
            oom_kind = classify_oom(lowered)
            if oom_kind == "cuda":
                oom = True
                send(
                    {
                        "kind": "error",
                        "code": "SVC-5001",
                        "message": "显存不足",
                        "cause": line,
                    }
                )
                break
            if oom_kind == "host":
                host_oom = True
                send(
                    {
                        "kind": "log",
                        "level": "error",
                        "message": "内存不足：这是主机 RAM 不足（不是显存），请关闭其他程序后重试",
                    }
                )
                break
            parsed = _parse_step(line)
            if parsed and parsed.get("diverged"):
                diverged = True
                send(
                    {
                        "kind": "error",
                        "code": "SVC-5002",
                        "message": "训练发散（NaN/Inf）",
                        "cause": line,
                    }
                )
                break
            if parsed:
                last_step = max(last_step, int(parsed["step"]))
                total = max_steps if max_steps else 0
                send(
                    {
                        "kind": "progress",
                        "stage": kind,
                        "step": last_step,
                        "total": total,
                        "losses": parsed["losses"],
                        "lr": parsed.get("lr"),
                    }
                )
                if max_steps and last_step >= max_steps:
                    stopped_for_smoke = True
                    break
            else:
                epoch = EPOCH_RE.search(line)
                if epoch:
                    send({"kind": "log", "level": "info", "message": line})
                elif line.strip():
                    send({"kind": "log", "level": "debug", "message": line})
            if timeout_seconds and time.time() - started > timeout_seconds:
                send(
                    {
                        "kind": "error",
                        "code": "SVC-5004",
                        "message": "训练超时",
                        "cause": f"{timeout_seconds}s",
                    }
                )
                break
    finally:
        try:
            log_file.close()
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass

    exit_code = proc.returncode

    # 驱动重置 / 进程被外部终止：没有 traceback、没有 OOM/发散，且已有 checkpoint
    # → 自动从最近 checkpoint 续训一次，避免整晚白跑
    if (
        retry_crash
        and exit_code not in (0, None)
        and not saw_traceback
        and not oom
        and not host_oom
        and not diverged
        and not stopped_for_smoke
    ):
        send(
            {
                "kind": "log",
                "level": "warn",
                "message": f"训练进程异常退出（exit={exit_code}，无异常栈，常见于显卡驱动重置），已自动续训一次",
            }
        )
        data = run_train(
            project,
            upstream,
            config_path,
            model_dir,
            max_steps,
            kind,
            emit,
            train_script,
            timeout_seconds,
            retry_oom=False,
            retry_crash=False,
            _emit_result=False,
        )
        data["retriedAfterCrash"] = True
        if _emit_result:
            send({"kind": "result", "data": data})
        return data

    if stopped_for_smoke:
        exit_code = 0

    data = {
        "exitCode": exit_code,
        "lastStep": last_step,
        "oom": oom,
        "hostOom": host_oom,
        "diverged": diverged,
        "stoppedForSmoke": stopped_for_smoke,
        "elapsedSeconds": round(time.time() - started, 1),
        "modelDir": model_dir,
        "finishedAt": _now(),
    }
    # 主机内存不足（不是显存）：减 batch 没用，直接给出可操作提示
    if host_oom and not oom:
        send(
            {
                "kind": "log",
                "level": "error",
                "message": "内存不足：这是主机 RAM 不足（不是显存）。请关闭其他程序，"
                "或在设置里把并发降下来后重试；减 batch_size 对这种情况无效。",
            }
        )

    # OOM 自愈：降一半 batch_size 后重试一次；只重试一次，避免死循环。
    if oom and retry_oom:
        cfg_abs = Path(config_path)
        if not cfg_abs.is_absolute():
            cfg_abs = Path(project) / config_path
        new_size = halve_batch_size(str(cfg_abs))
        if new_size is not None:
            send(
                {
                    "kind": "log",
                    "level": "warn",
                    "message": f"显存不足，已把 batch_size 降到 {new_size} 并重试一次",
                }
            )
            data = run_train(
                project,
                upstream,
                config_path,
                model_dir,
                max_steps,
                kind,
                emit,
                train_script,
                timeout_seconds,
                retry_oom=False,
                _emit_result=False,
            )
            data["retried"] = True
            data["oomDetected"] = True
            data["batchSizeAfterRetry"] = new_size
        else:
            send(
                {
                    "kind": "log",
                    "level": "error",
                    "message": "显存不足且 batch_size 无法再降（或配置缺字段），不再重试",
                }
            )

    if _emit_result:
        send({"kind": "result", "data": data})
    return data


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge train")
    parser.add_argument("--project", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--config", default="configs/config.json")
    parser.add_argument("--model", default="44k")
    parser.add_argument("--kind", choices=["main", "diffusion"], default="main")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--smoke-steps", type=int, default=100)
    args = parser.parse_args(argv)

    from ..rpc import emit

    config_path = args.config
    model_dir = args.model
    max_steps: Optional[int] = None

    if args.smoke:
        project = Path(args.project)
        source = project / args.config
        smoke_config = project / "reports" / "smoke_config.json"
        build_smoke_config(str(source), str(smoke_config))
        config_path = str(smoke_config)
        model_dir = "_smoke"
        max_steps = args.smoke_steps

    data = run_train(
        args.project,
        args.upstream,
        config_path,
        model_dir,
        max_steps=max_steps,
        kind=args.kind,
        emit=lambda event: emit(event.pop("kind"), **event),
    )
    if data["oom"] or data["diverged"]:
        return exit_codes.TRAIN
    if data["exitCode"] not in (0, None):
        return exit_codes.TRAIN
    return exit_codes.OK
