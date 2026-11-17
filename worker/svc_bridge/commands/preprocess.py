"""预处理三步编排（D1/D2/D5）。

上游三个脚本都按"相对 cwd 的路径 + 逐文件幂等"设计，因此：
- 每一步都是独立子进程，cwd = 项目根；
- 取消/失败后重跑天然跳过已完成文件（脚本自己判断 .soft.pt/.f0.npy/.spec.pt 是否存在）；
- 我们只负责：命令构造、退出码、产物计数、差异清单、manifest 落盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import exit_codes

Runner = Callable[[List[str], Path, Dict[str, str]], int]

SUFFIX_KINDS = [
    (".wav", "wav"),
    (".soft.pt", "soft"),
    (".f0.npy", "f0"),
    (".spec.pt", "spec"),
    (".vol.npy", "vol"),
    (".mel.npy", "mel"),
    (".aug_mel.npy", "aug_mel"),
    (".aug_vol.npy", "aug_vol"),
]

MANIFEST_SCHEMA = "svcstudio.preprocess/1"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_steps(
    project: str,
    upstream: str,
    encoder: str = "vec768l12",
    f0: str = "rmvpe",
    num_processes: int = 1,
    use_diff: bool = False,
    vol_aug: bool = False,
    python: Optional[str] = None,
) -> List[Dict[str, Any]]:
    py = python or "python"
    up = Path(upstream)
    project_path = Path(project)

    resample = [py, str(up / "resample.py"), "--sr2", "44100", "--skip_loudnorm"]
    flist = [py, str(up / "preprocess_flist_config.py"), "--speech_encoder", encoder]
    if vol_aug:
        flist.append("--vol_aug")
    hubert = [
        py,
        str(up / "preprocess_hubert_f0.py"),
        "--f0_predictor",
        f0,
        "--num_processes",
        str(num_processes),
    ]
    if use_diff:
        hubert.append("--use_diff")

    return [
        {"name": "resample", "argv": resample, "cwd": project_path},
        {"name": "flist_config", "argv": flist, "cwd": project_path},
        {"name": "hubert_f0", "argv": hubert, "cwd": project_path},
    ]


def count_products(dataset_dir: Path) -> Dict[str, int]:
    counts = {kind: 0 for _, kind in SUFFIX_KINDS}
    if not dataset_dir.is_dir():
        return counts
    for path in dataset_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        for suffix, kind in SUFFIX_KINDS:
            if name.endswith(suffix):
                counts[kind] += 1
                break
    return counts


def diff_products(counts: Dict[str, int], use_diff: bool, vol_embedding: bool) -> List[Dict[str, Any]]:
    base = counts.get("wav", 0)
    expected = {"soft": base, "f0": base, "spec": base}
    if use_diff or vol_embedding:
        expected["vol"] = base
    if use_diff:
        expected["mel"] = base
        expected["aug_mel"] = base
        expected["aug_vol"] = base

    diffs: List[Dict[str, Any]] = []
    for kind, want in expected.items():
        have = counts.get(kind, 0)
        if have != want:
            diffs.append(
                {
                    "kind": kind,
                    "expected": want,
                    "actual": have,
                    "missing": max(0, want - have),
                }
            )
    return diffs


def _dataset_dir(project: Path) -> Path:
    return project / "dataset" / "44k"


def _min_clip_seconds(dataset_dir: Path) -> float:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from svc_bridge.audio import read_wav

    durations = []
    for wav in dataset_dir.glob("*/*.wav"):
        try:
            durations.append(read_wav(wav).seconds)
        except Exception:  # noqa: BLE001
            continue
    return min(durations) if durations else 0.0


def run_chain(
    project: str,
    upstream: str,
    encoder: str = "vec768l12",
    f0: str = "rmvpe",
    num_processes: int = 1,
    use_diff: bool = False,
    vol_aug: bool = False,
    runner: Optional[Runner] = None,
    manifest_path: Optional[str] = None,
) -> Dict[str, Any]:
    project_path = Path(project)
    steps = build_steps(project, upstream, encoder, f0, num_processes, use_diff, vol_aug)
    env = {
        "PYTHONPATH": str(Path(upstream)) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    executed: List[Dict[str, Any]] = []
    failed_step: Optional[str] = None
    for step in steps:
        started = _now()
        if runner is None:
            import subprocess

            code = subprocess.run(step["argv"], cwd=str(step["cwd"]), env={**os.environ, **env}).returncode
        else:
            code = runner(step["argv"], step["cwd"], env)
        finished = _now()
        executed.append(
            {
                "name": step["name"],
                "argv": step["argv"],
                "cwd": str(step["cwd"]),
                "startedAt": started,
                "finishedAt": finished,
                "exitCode": code,
            }
        )
        if code != 0:
            failed_step = step["name"]
            break

    dataset_dir = _dataset_dir(project_path)
    counts = count_products(dataset_dir)
    config_path = project_path / "configs" / "config.json"
    diffusion_path = project_path / "configs" / "diffusion.yaml"

    vol_embedding = False
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
            vol_embedding = bool(cfg.get("model", {}).get("vol_embedding", False))
        except Exception:  # noqa: BLE001
            vol_embedding = False

    diffs = diff_products(counts, use_diff, vol_embedding)
    min_clip = _min_clip_seconds(dataset_dir)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generatedAt": _now(),
        "project": str(project_path),
        "upstream": str(upstream),
        "params": {
            "speechEncoder": encoder,
            "f0Predictor": f0,
            "numProcesses": num_processes,
            "useDiff": use_diff,
            "volAug": vol_aug,
        },
        "steps": executed,
        "failedStep": failed_step,
        "counts": counts,
        "diffs": diffs,
        "minClipSeconds": round(min_clip, 3),
        "configSha256": _sha256(config_path) if config_path.is_file() else None,
        "diffusionSha256": _sha256(diffusion_path) if diffusion_path.is_file() else None,
        "passed": failed_step is None and not diffs and config_path.is_file(),
    }

    target = Path(manifest_path) if manifest_path else project_path / "reports" / "preprocess_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="svc_bridge preprocess")
    parser.add_argument("--project", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--encoder", default="vec768l12")
    parser.add_argument("--f0", default="rmvpe")
    parser.add_argument("--num-processes", type=int, default=1)
    parser.add_argument("--use-diff", action="store_true")
    parser.add_argument("--vol-aug", action="store_true")
    parser.add_argument("--manifest", default="")
    args = parser.parse_args(argv)

    manifest = run_chain(
        args.project,
        args.upstream,
        args.encoder,
        args.f0,
        args.num_processes,
        args.use_diff,
        args.vol_aug,
        manifest_path=args.manifest or None,
    )
    sys.stdout.buffer.write(json.dumps(manifest, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    if manifest["passed"]:
        return exit_codes.OK
    return exit_codes.PREPROCESS
