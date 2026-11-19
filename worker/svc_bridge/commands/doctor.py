"""环境自检（B1/B2）。每项都返回结构化结果，绝不抛异常。

设计要点：
- 单项失败不影响其他项，报告始终可解析；
- 每项都带 `fix`（可复制的修复动作 / 命令），因为"失败但不知道怎么修"等于没报；
- 不导入 torch 之外的重量级依赖；torch 用 try/except 延迟导入。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = "svcstudio.doctor/1"
EXPECTED_PYTHON = (3, 8, 9)

ENCODER_WEIGHTS = {
    "vec768l12": ["pretrain/checkpoint_best_legacy_500.pt"],
    "vec256l9": ["pretrain/checkpoint_best_legacy_500.pt"],
    "hubertsoft": ["pretrain/hubert-soft-0d54a1f4.pt"],
    "whisper-ppg": ["pretrain/medium.pt"],
    "whisper-ppg-large": ["pretrain/large-v2.pt"],
    "cnhubertlarge": ["pretrain/chinese-hubert-large-fairseq-ckpt.pt"],
    "dphubert": ["pretrain/DPHuBERT-sp0.75.pth"],
    "wavlmbase+": ["pretrain/WavLM-Base+.pt"],
}

F0_WEIGHTS = {
    "rmvpe": ["pretrain/rmvpe.pt"],
    "fcpe": ["pretrain/fcpe.pt"],
}

VOCoder_FILES = [
    # 上游加载器只用 model + config.json；README/checkpoint 属于发行包附带，不是运行必需。
    "pretrain/nsf_hifigan/model",
    "pretrain/nsf_hifigan/config.json",
]


def _item(
    check_id: str,
    state: str,
    message: str,
    *,
    cause: str = "",
    fix: str = "",
    detail: Optional[Dict[str, Any]] = None,
    blocking: bool = False,
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "state": state,
        "message": message,
        "cause": cause,
        "fix": fix,
        "detail": detail or {},
        "blocking": blocking,
    }


GPU_PYTHON = (3, 13)


def check_python() -> Dict[str, Any]:
    """唯一运行时是 Python 3.13（torch cu128 要求）。

    系统 Python 已由 Rust 侧 ensure_bundled() 拒绝，不会走到这里。
    """
    actual = tuple(sys.version_info[:3])
    expected_text = ".".join(map(str, GPU_PYTHON))
    if actual[:2] == GPU_PYTHON[:2]:
        return _item(
            "python",
            "pass",
            f"内置 Python {actual[0]}.{actual[1]}.{actual[2]} 可用",
            detail={"version": ".".join(map(str, actual)), "expected": expected_text},
        )
    return _item(
        "python",
        "warn",
        f"内置运行时 Python 版本不符合基线（期望 {expected_text}）",
        cause="检测到 " + ".".join(map(str, actual)),
        fix="重新安装内置运行时；不要用系统 Python 启动 worker",
        detail={"version": ".".join(map(str, actual)), "expected": expected_text},
    )


def check_torch() -> Dict[str, Any]:
    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 任何导入错误都要变成可读结论
        return _item(
            "torch",
            "fail",
            "torch 不可用",
            cause=str(exc),
            fix="重新运行安装包修复运行时，或按版本矩阵重装 torch",
            blocking=True,
        )
    return _item("torch", "pass", "torch 可导入", detail={"version": torch.__version__})


def check_cuda() -> Dict[str, Any]:
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        return _item("cuda", "fail", "无法检测 CUDA（torch 缺失）", cause=str(exc), blocking=True)
    try:
        available = bool(torch.cuda.is_available())
        version = torch.version.cuda
    except Exception as exc:  # noqa: BLE001
        return _item("cuda", "fail", "CUDA 状态读取失败", cause=str(exc), blocking=True)
    if not available:
        return _item(
            "cuda",
            "fail",
            "CUDA 不可用",
            cause="torch.cuda.is_available() 为 False",
            fix="安装 NVIDIA 驱动；核对 torch 的 CUDA 版本与驱动匹配",
            detail={"torch_cuda": version},
            blocking=True,
        )
    return _item("cuda", "pass", "CUDA 可用", detail={"torch_cuda": version})


def check_gpu() -> Dict[str, Any]:
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        return _item("gpu", "fail", "无法枚举显卡（torch 缺失）", cause=str(exc), blocking=True)
    try:
        if not torch.cuda.is_available():
            return _item(
            "gpu",
            "fail",
            "没有可用的 NVIDIA 显卡",
            cause="torch.cuda.is_available() 为 False",
            fix="接入 NVIDIA 显卡并安装驱动；无显卡时只能用 CPU 推理，不能训练",
            blocking=True,
        )
        props = torch.cuda.get_device_properties(0)
        vram_mb = int(props.total_memory / (1024 * 1024))
    except Exception as exc:  # noqa: BLE001
        return _item("gpu", "fail", "显卡信息读取失败", cause=str(exc), blocking=True)
    state = "pass" if vram_mb >= 6144 else "fail"
    return _item(
        "gpu",
        state,
        f"检测到 {props.name}（{vram_mb} MB）",
        cause="" if state == "pass" else "显存低于 6GB，训练不可用",
        fix="" if state == "pass" else "仅使用推理；或换 6GB 以上显卡",
        detail={"name": props.name, "vram_mb": vram_mb},
        blocking=True,
    )


def check_ffmpeg() -> Dict[str, Any]:
    bundled = os.environ.get("SVCSTUDIO_FFMPEG", "").strip()
    path = bundled if bundled and Path(bundled).is_file() else shutil.which("ffmpeg")
    if path:
        return _item("ffmpeg", "pass", "ffmpeg 可用", detail={"path": path})
    return _item(
        "ffmpeg",
        "fail",
        "找不到 ffmpeg",
        cause="PATH 中没有 ffmpeg",
        fix="重新运行安装包修复（内置 ffmpeg 丢失），或安装 ffmpeg 并加入 PATH",
        blocking=True,
    )


def check_disk(data_root: str) -> Dict[str, Any]:
    try:
        usage = shutil.disk_usage(data_root if os.path.isdir(data_root) else os.path.dirname(data_root) or ".")
        free_gb = round(usage.free / (1024**3), 1)
    except Exception as exc:  # noqa: BLE001
        return _item("disk", "warn", "磁盘余量无法读取", cause=str(exc), detail={"data_root": data_root})
    state = "pass" if free_gb >= 20 else "warn"
    return _item(
        "disk",
        state,
        f"数据盘剩余 {free_gb} GB",
        cause="" if state == "pass" else "低于建议的 20GB",
        fix="" if state == "pass" else "清理旧项目/日志，或把数据目录换到空间更大的盘",
        detail={"free_gb": free_gb, "data_root": data_root},
    )


def _check_files(check_id: str, base: str, rels: List[str], what: str) -> Dict[str, Any]:
    missing = [r for r in rels if not os.path.isfile(os.path.join(base, r))]
    if not missing:
        return _item(check_id, "pass", f"{what} 已就绪", detail={"files": rels})
    return _item(
        check_id,
        "fail",
        f"缺少{what}",
        cause="缺失文件：" + ", ".join(missing),
        fix="打开资产中心导入对应权重（离线可用），或下载后放入 pretrain 目录",
        detail={"missing": missing},
        blocking=True,
    )


def build_report(
    data_root: str,
    encoder: str = "vec768l12",
    f0: str = "rmvpe",
    pretrain: str = "",
) -> Dict[str, Any]:
    pretrain_dir = (
        pretrain
        or os.environ.get("SVCSTUDIO_PRETRAIN_DIR", "").strip()
        or os.path.join(data_root, "pretrain")
    )
    weights_root = os.path.dirname(os.path.abspath(pretrain_dir.rstrip("/\\")))
    items = [
        check_python(),
        check_torch(),
        check_cuda(),
        check_gpu(),
        check_ffmpeg(),
        check_disk(data_root),
        _check_files("encoder", weights_root, ENCODER_WEIGHTS.get(encoder, []), f"语音编码器（{encoder}）"),
        _check_files("f0", weights_root, F0_WEIGHTS.get(f0, []), f"F0 权重（{f0}）"),
        _check_files("vocoder", weights_root, VOCoder_FILES, "声码器 nsf_hifigan"),
    ]
    summary = {
        "pass": sum(1 for i in items if i["state"] == "pass"),
        "warn": sum(1 for i in items if i["state"] == "warn"),
        "fail": sum(1 for i in items if i["state"] == "fail"),
        "trainable": not any(i["state"] == "fail" and i["blocking"] for i in items),
    }
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_root": data_root,
        "items": items,
        "summary": summary,
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge doctor")
    parser.add_argument("--json", action="store_true", help="以单个 JSON 对象输出")
    parser.add_argument("--data-root", default=".", help="数据根目录（磁盘检查）")
    parser.add_argument("--pretrain-dir", default="", help="权重目录（默认 <data-root>/pretrain）")
    parser.add_argument("--encoder", default="vec768l12")
    parser.add_argument("--f0", default="rmvpe")
    args = parser.parse_args(argv)

    report = build_report(args.data_root, args.encoder, args.f0, args.pretrain_dir)
    if args.json:
        # 直接写 UTF-8 字节：Windows 控制台默认 cp936，按文本 print 会把中文编坏。
        payload = json.dumps(report, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(payload + b"\n")
        sys.stdout.buffer.flush()
    else:
        for item in report["items"]:
            print(f"[{item['state']:4}] {item['id']}: {item['message']}")
            if item["fix"]:
                print(f"       fix: {item['fix']}")
    return 0
