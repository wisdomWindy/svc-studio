"""模型压缩与 ONNX 导出入口（W7-6）。

两者都是"调用上游脚本 + 校验产物"；ONNX 导出需要临时改上游脚本里的 path 常量，
所以复制一份到项目 tmp 目录再改，绝不改 vendor 源码。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import exit_codes


def build_compress_argv(
    upstream: str,
    config_path: str,
    input_model: str,
    output_model: str,
    half: bool = False,
    python: Optional[str] = None,
) -> List[str]:
    py = python or "python"
    argv = [
        py,
        str(Path(upstream) / "compress_model.py"),
        "-c",
        config_path,
        "-i",
        input_model,
        "-o",
        output_model,
    ]
    if half:
        argv.append("-hf")
    return argv


def patch_onnx_script(source: Path, target: Path, project_name: str) -> Path:
    """复制 onnx_export.py 并把 path 常量替换成项目名；vendor 源码保持只读。"""
    text = source.read_text(encoding="utf-8")
    patched, count = re.subn(r'path\s*=\s*"[^"]*"', f'path = "{project_name}"', text, count=1)
    if count == 0:
        raise ValueError("onnx_export.py 中找不到 path 常量")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(patched, encoding="utf-8")
    return target


def run_compress(
    project: str,
    upstream: str,
    config_path: str,
    input_model: str,
    output_model: str,
    half: bool = False,
    python: Optional[str] = None,
) -> Dict[str, Any]:
    started = time.time()
    argv = build_compress_argv(upstream, config_path, input_model, output_model, half, python)
    from ..procwin import no_window_kwargs

    proc = subprocess.run(
        argv,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **no_window_kwargs(),
    )
    out = Path(project) / output_model if not Path(output_model).is_absolute() else Path(output_model)
    result = {
        "ok": proc.returncode == 0 and out.is_file(),
        "exitCode": proc.returncode,
        "output": str(out),
        "outputExists": out.is_file(),
        "bytes": out.stat().st_size if out.is_file() else 0,
        "elapsedSeconds": round(time.time() - started, 1),
        "stdoutTail": (proc.stdout or "")[-2000:],
        "stderrTail": (proc.stderr or "")[-2000:],
    }
    if result["ok"] is False and "没有生成" not in str(result):
        result["error"] = "压缩脚本失败或没有产出文件" if proc.returncode == 0 else "压缩脚本非零退出"
    return result


def run_onnx_export(
    project: str,
    upstream: str,
    model_name: str,
    python: Optional[str] = None,
) -> Dict[str, Any]:
    """约定：checkpoints/<name>/model.pth + config.json；产物 model.onnx。"""
    project_path = Path(project)
    work = project_path / "tmp" / "onnx"
    work.mkdir(parents=True, exist_ok=True)
    source = Path(upstream) / "onnx_export.py"
    try:
        script = patch_onnx_script(source, work / "onnx_export.py", model_name)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"无法准备导出脚本：{exc}"}

    argv = [python or "python", str(script)]
    proc = subprocess.run(
        argv, cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    onnx_path = project_path / "checkpoints" / model_name / "model.onnx"
    result = {
        "ok": proc.returncode == 0 and onnx_path.is_file(),
        "exitCode": proc.returncode,
        "output": str(onnx_path),
        "outputExists": onnx_path.is_file(),
        "stdoutTail": (proc.stdout or "")[-2000:],
        "stderrTail": (proc.stderr or "")[-2000:],
        "experimental": True,
    }
    if not result["ok"]:
        result["error"] = "ONNX 导出失败（该能力标注为实验性）"
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge modeltool")
    parser.add_argument("action", choices=["compress", "onnx"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--config", default="configs/config.json")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--half", action="store_true")
    args = parser.parse_args(argv)

    if args.action == "compress":
        result = run_compress(
            args.project, args.upstream, args.config, args.input, args.output, args.half
        )
    else:
        result = run_onnx_export(args.project, args.upstream, args.name)

    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return exit_codes.OK if result.get("ok") else exit_codes.INFER
