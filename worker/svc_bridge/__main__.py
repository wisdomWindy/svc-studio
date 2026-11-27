"""进程入口。

用法：
  python -m svc_bridge ping
  python -m svc_bridge rpc --job <id>
  python -m svc_bridge doctor --json
  python -m svc_bridge dataset scan|qc|slice|resample --root <dir>
  python -m svc_bridge config validate|assert --config <f> --diffusion <f>
  python -m svc_bridge preprocess --project <dir> --upstream <dir>
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from . import exit_codes
from .rpc import PROTOCOL_VERSION, emit, emit_log, read_control


def _cmd_ping() -> int:
    emit(
        "hello",
        protocol=PROTOCOL_VERSION,
        bridge=__version__,
        python=".".join(str(p) for p in sys.version_info[:3]),
    )
    return exit_codes.OK


def _cmd_rpc(job_id: str) -> int:
    emit("hello", protocol=PROTOCOL_VERSION, bridge=__version__, job_id=job_id)
    while True:
        msg = read_control()
        if msg is None:
            emit_log("control channel closed")
            break
        cmd = msg.get("cmd")
        if cmd == "cancel":
            emit("cancelled")
            return exit_codes.CANCELLED
        if cmd == "stop":
            emit("stopped")
            return exit_codes.OK
        if cmd == "ping":
            emit("pong", job_id=job_id)
            continue
        emit_log("unknown control command: %r" % (cmd,), level="warn")
    return exit_codes.OK


def _add_dataset(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["scan", "qc", "slice", "resample", "assert", "convert", "separate", "finalize", "prune-short", "declick", "prune-quality", "refine"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--min-seconds", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=15.0)
    parser.add_argument("--min-interval-ms", type=int, default=300)
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--fade-ms", type=float, default=8.0)
    parser.add_argument("--keep", type=float, default=0.85)
    parser.add_argument("--min-snr-db", type=float, default=10.0)
    parser.add_argument("--min-rolloff-hz", type=float, default=0.0)
    parser.add_argument("--dedup-similarity", type=float, default=0.995)
    parser.add_argument("--target-rms-dbfs", type=float, default=-20.0)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--events", action="store_true", help="逐文件输出进度事件")
    parser.add_argument(
        "--keep-rejected",
        action="store_true",
        help="体检不合格的源文件保留在磁盘上（默认直接删除，只留合格音频）",
    )


def _add_doctor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--data-root", default=".")
    parser.add_argument("--pretrain-dir", default="")
    parser.add_argument("--encoder", default="vec768l12")
    parser.add_argument("--f0", default="rmvpe")


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["validate", "assert"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--diffusion", required=True)
    parser.add_argument("--dataset", default="")


def _add_preprocess(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--encoder", default="vec768l12")
    parser.add_argument("--f0", default="rmvpe")
    parser.add_argument("--num-processes", type=int, default=1)
    parser.add_argument("--use-diff", action="store_true")
    parser.add_argument("--vol-aug", action="store_true")
    parser.add_argument("--manifest", default="")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="svc_bridge")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="协议握手")
    rpc_parser = sub.add_parser("rpc", help="事件循环")
    rpc_parser.add_argument("--job", required=True, help="job id")
    _add_doctor(sub.add_parser("doctor", help="环境自检"))
    _add_dataset(sub.add_parser("dataset", help="数据集扫描/QC/切片/重采样"))
    _add_config(sub.add_parser("config", help="配置校验与前置断言"))
    _add_preprocess(sub.add_parser("preprocess", help="预处理三步编排"))
    infer_parser = sub.add_parser("infer", help="推理批次（事件流）")
    infer_parser.add_argument("--project", required=True)
    infer_parser.add_argument("--model", required=True)
    infer_parser.add_argument("--config", required=True)
    infer_parser.add_argument("--params", required=True)
    infer_parser.add_argument("--spk", required=True)
    infer_parser.add_argument("--inputs", nargs="+", required=True)
    infer_parser.add_argument("--batch-name", default="batch")

    tool_parser = sub.add_parser("modeltool", help="压缩/ONNX 导出")
    tool_parser.add_argument("action", choices=["compress", "onnx"])
    tool_parser.add_argument("--project", required=True)
    tool_parser.add_argument("--upstream", required=True)
    tool_parser.add_argument("--config", default="configs/config.json")
    tool_parser.add_argument("--input", default="")
    tool_parser.add_argument("--output", default="")
    tool_parser.add_argument("--name", default="")
    tool_parser.add_argument("--half", action="store_true")

    mix_parser = sub.add_parser("mix", help="声线混合计划校验")
    mix_parser.add_argument("--input", required=True)

    model_parser = sub.add_parser("model", help="模型兼容性判定")
    model_parser.add_argument("action", choices=["check"])
    model_parser.add_argument("--model", required=True)
    model_parser.add_argument("--config", required=True)

    aux_parser = sub.add_parser("aux", help="聚类/特征检索索引")
    aux_parser.add_argument("--project", required=True)
    aux_parser.add_argument("--upstream", required=True)
    aux_parser.add_argument("--kind", choices=["cluster", "index"], required=True)
    train_parser = sub.add_parser("train", help="训练/冒烟（事件流）")
    train_parser.add_argument("--project", required=True)
    train_parser.add_argument("--upstream", required=True)
    train_parser.add_argument("--config", default="configs/config.json")
    train_parser.add_argument("--model", default="44k")
    train_parser.add_argument("--kind", choices=["main", "diffusion"], default="main")
    train_parser.add_argument("--smoke", action="store_true")
    train_parser.add_argument("--smoke-steps", type=int, default=100)

    args = parser.parse_args(argv)

    if args.command == "ping":
        return _cmd_ping()
    if args.command == "rpc":
        return _cmd_rpc(str(args.job))

    if args.command == "doctor":
        from .commands import doctor

        doctor_argv: List[str] = [
        "--pretrain-dir",
        getattr(args, "pretrain_dir", "") or "",
            "--data-root",
            str(args.data_root),
            "--encoder",
            str(args.encoder),
            "--f0",
            str(args.f0),
        ]
        if args.json:
            doctor_argv.insert(0, "--json")
        return doctor.main(doctor_argv)

    if args.command == "dataset":
        from .commands import dataset

        dataset_argv: List[str] = [
            str(args.action),
            "--root",
            str(args.root),
            "--min-seconds",
            str(args.min_seconds),
            "--max-seconds",
            str(args.max_seconds),
            "--min-interval-ms",
            str(args.min_interval_ms),
            "--sample-rate",
            str(args.sample_rate),
            # 这几个必须一起转发：少了就会静默退回默认值（曾导致 --keep 1.0 被忽略、
            # prune-quality 仍剔除 15% 的文件）
            "--keep",
            str(args.keep),
            "--fade-ms",
            str(args.fade_ms),
            "--min-snr-db",
            str(args.min_snr_db),
            "--min-rolloff-hz",
            str(args.min_rolloff_hz),
            "--dedup-similarity",
            str(args.dedup_similarity),
            "--target-rms-dbfs",
            str(args.target_rms_dbfs),
            *(["--no-normalize"] if getattr(args, "no_normalize", False) else []),
            *(["--keep-rejected"] if getattr(args, "keep_rejected", False) else []),
            *(["--events"] if getattr(args, "events", False) else []),
        ]
        if args.backup_dir:
            dataset_argv.extend(["--backup-dir", str(args.backup_dir)])
        if args.out:
            dataset_argv.extend(["--out", str(args.out)])
        return dataset.main(dataset_argv)

    if args.command == "config":
        from .commands import config as config_cmd

        config_argv: List[str] = [
            str(args.action),
            "--config",
            str(args.config),
            "--diffusion",
            str(args.diffusion),
        ]
        if args.dataset:
            config_argv.extend(["--dataset", str(args.dataset)])
        return config_cmd.main(config_argv)

    if args.command == "preprocess":
        from .commands import preprocess

        preprocess_argv: List[str] = [
            "--project",
            str(args.project),
            "--upstream",
            str(args.upstream),
            "--encoder",
            str(args.encoder),
            "--f0",
            str(args.f0),
            "--num-processes",
            str(args.num_processes),
        ]
        if args.use_diff:
            preprocess_argv.append("--use-diff")
        if args.vol_aug:
            preprocess_argv.append("--vol-aug")
        if args.manifest:
            preprocess_argv.extend(["--manifest", str(args.manifest)])
        return preprocess.main(preprocess_argv)

    if args.command == "train":
        from .commands import train

        train_argv: List[str] = [
            "--project",
            str(args.project),
            "--upstream",
            str(args.upstream),
            "--config",
            str(args.config),
            "--model",
            str(args.model),
            "--kind",
            str(args.kind),
            "--smoke-steps",
            str(args.smoke_steps),
        ]
        if args.smoke:
            train_argv.append("--smoke")
        return train.main(train_argv)

    if args.command == "aux":
        from .commands import extras

        return extras.main(
            [
                "--project",
                str(args.project),
                "--upstream",
                str(args.upstream),
                "--kind",
                str(args.kind),
            ]
        )

    if args.command == "mix":
        from .commands import mix as mix_cmd

        return mix_cmd.main(["--input", str(args.input)])

    if args.command == "model":
        from .commands import model

        return model.main(
            [str(args.action), "--model", str(args.model), "--config", str(args.config)]
        )

    if args.command == "infer":
        from .commands import infer_cli

        infer_argv: List[str] = [
            "--project",
            str(args.project),
            "--model",
            str(args.model),
            "--config",
            str(args.config),
            "--params",
            str(args.params),
            "--spk",
            str(args.spk),
            "--batch-name",
            str(args.batch_name),
            "--inputs",
        ]
        infer_argv.extend(str(p) for p in args.inputs)
        return infer_cli.main(infer_argv)

    if args.command == "modeltool":
        from .commands import modeltool

        tool_argv: List[str] = [
            str(args.action),
            "--project",
            str(args.project),
            "--upstream",
            str(args.upstream),
            "--config",
            str(args.config),
        ]
        for flag in ("input", "output", "name"):
            value = getattr(args, flag)
            if value:
                tool_argv.extend([f"--{flag}", str(value)])
        if args.half:
            tool_argv.append("--half")
        return modeltool.main(tool_argv)

    return exit_codes.USAGE


if __name__ == "__main__":
    raise SystemExit(main())
