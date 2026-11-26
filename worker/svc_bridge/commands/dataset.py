"""数据集：结构校验、QC 指标、切片、重采样、阈值断言。

口径（与 PRD §5.4 一致）：
- 时长：帧数/采样率；
- 静音比：10ms 帧 RMS 低于 -40dBFS 的帧占比；
- 削波：连续 ≥3 个样本达到 99.9% 满刻度的样本占比；
- 响度：整体 RMS dBFS（不引入 pyloudnorm；报告里写明 method=rms）；
- 阻断：单条 >30s、静音比 >20%、削波 ≥0.1%、不可解码、采样率/声道未统一。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from .. import exit_codes
from ..audio import (
    clipping_ratio,
    fade_edges,
    find_quietest_cut,
    frame_rms,
    peak_dbfs,
    protect_peak,
    read_wav,
    resample_linear,
    rms_dbfs,
    silence_ratio,
    trim_silence,
    to_mono_if_needed,
    write_wav,
)

WAV_EXTS = {".wav", ".wave"}
AUDIO_EXTS = {".wav", ".wave", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}
MAX_CLIP_SECONDS = 30.0
SILENCE_LIMIT = 0.20
CLIP_LIMIT = 0.001
TARGET_SR = 44100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_entry(path: Path, rel: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"relPath": rel, "status": "ok", "issues": []}
    try:
        wav = read_wav(path)
    except Exception as exc:  # noqa: BLE001
        entry["status"] = "corrupt"
        entry["issues"].append({"code": "SVC-3001", "message": f"无法解码：{exc}"})
        return entry

    entry.update(
        {
            "seconds": round(wav.seconds, 3),
            "sampleRate": wav.sample_rate,
            "channels": wav.channels,
            "peakDbfs": round(peak_dbfs(to_mono_if_needed(wav)), 2),
        }
    )
    if wav.seconds > MAX_CLIP_SECONDS:
        entry["issues"].append(
            {"code": "SVC-3005", "message": f"单条 {wav.seconds:.1f}s 超过 {MAX_CLIP_SECONDS:.0f}s"}
        )
    if wav.sample_rate != TARGET_SR:
        entry["issues"].append(
            {"code": "SVC-3005", "message": f"采样率 {wav.sample_rate} 需转为 {TARGET_SR}"}
        )
    if wav.channels != 1:
        entry["issues"].append({"code": "SVC-3005", "message": f"{wav.channels} 声道需转单声道"})
    return entry


def scan(root: str) -> Dict[str, Any]:
    base = Path(root)
    files: List[Dict[str, Any]] = []
    non_wav: List[Dict[str, str]] = []
    empty_dirs: List[str] = []
    nested: List[str] = []
    speakers: List[str] = []

    if not base.is_dir():
        return {
            "schema": "svcstudio.dataset.scan/1",
            "generatedAt": _now(),
            "root": root,
            "speakers": [],
            "files": [],
            "nonWav": [],
            "emptyDirs": [root],
            "nested": [],
            "blocking": [
                {
                    "code": "SVC-3002",
                    "message": "数据集目录不存在",
                    "detail": "expect dataset_raw/<speaker>/*.wav",
                }
            ],
        }

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        speakers.append(spk.name)
        entries = list(spk.iterdir())
        if not any(e.is_file() for e in entries):
            empty_dirs.append(spk.name)
        for entry in entries:
            rel = f"{spk.name}/{entry.name}"
            if entry.is_dir():
                nested.append(rel)
                continue
            if entry.name.startswith("."):
                continue
            ext = entry.suffix.lower()
            if ext in WAV_EXTS:
                if entry.stat().st_size == 0:
                    files.append(
                        {
                            "relPath": rel,
                            "status": "corrupt",
                            "issues": [{"code": "SVC-3001", "message": "0 字节文件"}],
                        }
                    )
                else:
                    files.append(_file_entry(entry, rel))
            elif ext in AUDIO_EXTS:
                non_wav.append(
                    {
                        "relPath": rel,
                        "ext": ext,
                        "suggestion": "用 ffmpeg 转为 44100Hz 单声道 wav 后重新导入",
                    }
                )

    blocking: List[Dict[str, Any]] = []
    if not speakers:
        blocking.append({"code": "SVC-3002", "message": "没有任何说话人目录"})
    if nested:
        blocking.append(
            {
                "code": "SVC-3002",
                "message": f"{len(nested)} 个多层嵌套目录",
                "detail": "；".join(nested[:5]),
            }
        )
    corrupt = [f for f in files if f.get("status") == "corrupt"]
    if corrupt:
        blocking.append(
            {
                "code": "SVC-3001",
                "message": f"{len(corrupt)} 个文件无法解码",
                "detail": "；".join(f["relPath"] for f in corrupt[:5]),
            }
        )

    return {
        "schema": "svcstudio.dataset.scan/1",
        "generatedAt": _now(),
        "root": root,
        "speakers": speakers,
        "files": files,
        "nonWav": non_wav,
        "emptyDirs": empty_dirs,
        "nested": nested,
        "blocking": blocking,
    }


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[idx]



def prune_short(root: str, min_seconds: float = 1.0) -> Dict[str, Any]:
    """删除过短切片（默认 <1s）：这类切片会让训练对齐断言失败，且对音色学习无益。"""
    base = Path(root)
    removed = []
    for wav in sorted(base.rglob("*.wav")):
        try:
            data = read_wav(wav)
        except Exception:  # noqa: BLE001
            continue
        if data.seconds < min_seconds:
            # 连带删除同名特征文件，避免产物计数不一致
            for suffix in (".soft.pt", ".f0.npy", ".spec.npy", ".vol.npy", ".mel.npy", ".aug_mel.npy", ".aug_vol.npy"):
                side = wav.with_name(wav.name + suffix)
                if side.exists():
                    side.unlink()
            wav.unlink()
            removed.append(str(wav.relative_to(base)))
    return {"removed": len(removed), "files": removed[:200], "minSeconds": min_seconds}

def qc(root: str, on_progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    scanned = scan(root)
    base = Path(root)
    per_file: List[Dict[str, Any]] = []
    total_seconds = 0.0
    weighted_silence = 0.0
    weighted_clip = 0.0
    rms_values: List[float] = []

    total_files = len(scanned["files"])
    for index, f in enumerate(scanned["files"], start=1):
        if on_progress is not None:
            on_progress(index, total_files)
        if f.get("status") == "corrupt":
            continue
        path = base / f["relPath"]
        wav = read_wav(path)
        mono = to_mono_if_needed(wav)
        silence = silence_ratio(mono, wav.sample_rate)
        clip = clipping_ratio(mono)
        rms = rms_dbfs(mono)
        total_seconds += wav.seconds
        weighted_silence += silence * wav.seconds
        weighted_clip += clip * wav.seconds
        rms_values.append(rms)
        per_file.append(
            {
                "relPath": f["relPath"],
                "seconds": round(wav.seconds, 3),
                "sampleRate": wav.sample_rate,
                "channels": wav.channels,
                "silenceRatio": round(silence, 4),
                "clippingRatio": round(clip, 6),
                "rmsDbfs": round(rms, 2),
            }
        )

    durations = [f["seconds"] for f in per_file]
    sample_rates: Dict[str, int] = {}
    channels: Dict[str, int] = {}
    for f in per_file:
        sample_rates[str(f["sampleRate"])] = sample_rates.get(str(f["sampleRate"]), 0) + 1
        channels[str(f["channels"])] = channels.get(str(f["channels"]), 0) + 1

    report = {
        "schema": "svcstudio.qc/1",
        "generatedAt": _now(),
        "datasetRoot": root,
        "summary": {
            "fileCount": len(per_file),
            "totalSeconds": round(total_seconds, 2),
            "totalMinutes": round(total_seconds / 60.0, 2),
            "lowData": total_seconds < 30 * 60,
        },
        "metrics": {
            "duration": {
                "min": round(min(durations), 3) if durations else 0.0,
                "p50": round(_percentile(durations, 0.50), 3),
                "p95": round(_percentile(durations, 0.95), 3),
                "max": round(max(durations), 3) if durations else 0.0,
            },
            "sampleRate": sample_rates,
            "channels": channels,
            "silenceRatio": round(weighted_silence / total_seconds, 4) if total_seconds else 0.0,
            "clippingRatio": round(weighted_clip / total_seconds, 6) if total_seconds else 0.0,
            "loudness": {
                "method": "rms",
                "meanDbfs": round(sum(rms_values) / len(rms_values), 2) if rms_values else -120.0,
                "p10": round(_percentile(rms_values, 0.10), 2),
                "p90": round(_percentile(rms_values, 0.90), 2),
            },
        },
        "files": per_file,
        "scan": {
            "nonWav": scanned["nonWav"],
            "emptyDirs": scanned["emptyDirs"],
            "nested": scanned["nested"],
        },
    }
    assert_report = assert_thresholds(report)
    report["assertions"] = assert_report
    report["summary"]["passed"] = assert_report["passed"]
    report["summary"]["blocking"] = len(assert_report["blocking"])
    report["summary"]["warnings"] = len(assert_report["warnings"])
    return report


def assert_thresholds(report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = report["metrics"]
    blocking: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    if metrics["duration"]["max"] > MAX_CLIP_SECONDS:
        blocking.append(
            {
                "code": "SVC-3005",
                "message": f"存在超过 {MAX_CLIP_SECONDS:.0f}s 的音频",
                "detail": f"max={metrics['duration']['max']}s",
            }
        )
    if metrics["silenceRatio"] > SILENCE_LIMIT:
        blocking.append(
            {
                "code": "SVC-3005",
                "message": "静音比例过高",
                "detail": f"{metrics['silenceRatio']:.1%} > 20%",
            }
        )
    if metrics["clippingRatio"] >= CLIP_LIMIT:
        blocking.append(
            {
                "code": "SVC-3005",
                "message": "削波比例过高",
                "detail": f"{metrics['clippingRatio']:.3%} ≥ 0.1%",
            }
        )
    for rate, count in metrics["sampleRate"].items():
        if rate != str(TARGET_SR):
            blocking.append(
                {
                    "code": "SVC-3005",
                    "message": f"{count} 个文件采样率不是 {TARGET_SR}",
                    "detail": f"sampleRate={rate}",
                }
            )
    for ch, count in metrics["channels"].items():
        if ch != "1":
            blocking.append(
                {
                    "code": "SVC-3005",
                    "message": f"{count} 个文件不是单声道",
                    "detail": f"channels={ch}",
                }
            )

    for item in report["scan"]["nonWav"]:
        warnings.append(
            {
                "code": "SVC-3003",
                "message": f"非 wav 文件：{item['relPath']}",
                "detail": item["suggestion"],
            }
        )
    if report["summary"]["lowData"]:
        warnings.append(
            {
                "code": "SVC-3004",
                "message": "总时长低于 30 分钟",
                "detail": "训练前需要显式豁免，报告会标注 low_data",
            }
        )

    return {"passed": not blocking, "blocking": blocking, "warnings": warnings}


def _slice_one(path: Path, out_dir: Path, quarantine: Path, min_s: float, max_s: float) -> List[str]:
    wav = read_wav(path)
    mono_rate = wav.sample_rate
    frame_len = max(1, int(mono_rate * 0.01))
    samples = to_mono_if_needed(wav)
    frames = frame_rms(samples, frame_len)

    min_frames = max(1, int(min_s * 1000 / 10))
    max_frames = max(min_frames + 1, int(max_s * 1000 / 10))
    # 在 [min_s, min_s+1.5s] 里找最安静的位置下刀：找不到静音也不会硬切在元音中间
    extra_frames = int(1500 / 10)

    produced: List[str] = []
    start_frame = 0
    index = 1
    while start_frame < len(frames):
        remaining = len(frames) - start_frame
        if remaining * frame_len / mono_rate <= max_s:
            end_sample = len(samples)
            cut_frame = len(frames)
        else:
            search_hi = min(start_frame + min_frames + extra_frames, start_frame + max_frames)
            cut_frame, end_sample = find_quietest_cut(
                frames, start_frame, min_frames, search_hi, frame_len
            )
        chunk = samples[start_frame * frame_len : end_sample]
        if len(chunk) / mono_rate < 0.3:
            break
        # 切口两侧做 8ms 淡入淡出：硬切会在每个分片首尾留下一声"咔哒"
        chunk = fade_edges(chunk, mono_rate)
        out_path = out_dir / f"{path.stem}_{index:03d}.wav"
        write_wav(out_path, chunk, mono_rate)
        produced.append(out_path.name)
        start_frame = max(cut_frame, start_frame + 1)
        index += 1

    if produced:
        quarantine.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(quarantine / path.name))
    return produced


def slice_tree(
    root: str,
    min_s: float = 5.0,
    max_s: float = 15.0,
    min_interval_ms: int = 300,
) -> Dict[str, Any]:
    base = Path(root)
    produced: Dict[str, List[str]] = {}
    flagged: List[Dict[str, Any]] = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for wav_path in sorted(spk.glob("*.wav")):
            seconds = read_wav(wav_path).seconds
            if seconds <= max_s:
                continue
            names = _slice_one(
                wav_path,
                spk,
                base.parent / "quarantine" / spk.name,
                min_s,
                max_s,
            )
            if names:
                produced[f"{spk.name}/{wav_path.name}"] = names
                flagged.append(
                    {
                        "relPath": f"{spk.name}/{wav_path.name}",
                        "seconds": round(seconds, 2),
                        "chunks": len(names),
                    }
                )
    return {
        "schema": "svcstudio.dataset.slice/1",
        "generatedAt": _now(),
        "minSeconds": min_s,
        "maxSeconds": max_s,
        "minIntervalMs": min_interval_ms,
        "sliced": flagged,
        "outputs": produced,
    }


def _resample_with_ffmpeg(path: Path, target_sr: int) -> bool:
    """用 ffmpeg 就地重采样（swr 带抗混叠）。

    线性插值器不做低通，降采样会把高频折返成金属味/"电音"；有 ffmpeg 时一律走它。
    没有 ffmpeg 或转换失败时返回 False，调用方退回线性插值。
    """
    from ..procwin import no_window_kwargs
    from .dataset_convert import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        return False
    tmp = path.with_name(path.stem + ".resampling.wav")
    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(target_sr),
        "-c:a",
        "pcm_s16le",
        str(tmp),
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, **no_window_kwargs())
        if proc.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False


def resample_tree(root: str, target_sr: int = TARGET_SR, target_dbfs: float = -1.0) -> Dict[str, Any]:
    base = Path(root)
    changed: List[Dict[str, Any]] = []
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for wav_path in sorted(spk.glob("*.wav")):
            wav = read_wav(wav_path)
            if wav.sample_rate != target_sr and _resample_with_ffmpeg(wav_path, target_sr):
                wav = read_wav(wav_path)  # ffmpeg 已转成目标采样率单声道
            mono = to_mono_if_needed(wav)
            trimmed = trim_silence(mono, wav.sample_rate)
            resampled = resample_linear(trimmed, wav.sample_rate, target_sr)
            protected = protect_peak(resampled, target_dbfs)
            if wav.sample_rate == target_sr and wav.channels == 1 and len(protected) == len(wav.samples):
                continue
            write_wav(wav_path, protected, target_sr, 1)
            changed.append(
                {
                    "relPath": f"{spk.name}/{wav_path.name}",
                    "from": {"sampleRate": wav.sample_rate, "channels": wav.channels},
                    "to": {"sampleRate": target_sr, "channels": 1},
                }
            )
    return {
        "schema": "svcstudio.dataset.resample/1",
        "generatedAt": _now(),
        "targetSampleRate": target_sr,
        "peakCeilingDbfs": target_dbfs,
        "changed": changed,
    }


def write_report(report: Dict[str, Any], path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge dataset")
    parser.add_argument("action", choices=["scan", "qc", "slice", "resample", "assert", "convert", "separate", "finalize", "prune-short", "declick", "prune-quality"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--min-seconds", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=15.0)
    parser.add_argument("--min-interval-ms", type=int, default=300)
    parser.add_argument("--sample-rate", type=int, default=TARGET_SR)
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--fade-ms", type=float, default=8.0)
    parser.add_argument("--keep", type=float, default=0.85)
    parser.add_argument("--events", action="store_true", help="逐文件输出进度事件")
    args = parser.parse_args(argv)

    if args.action == "scan":
        result = scan(args.root)
    elif args.action == "prune-short":
        result = prune_short(args.root, 1.0)
    elif args.action == "prune-quality":
        from .dataset_quality import prune_quality_tree

        result = prune_quality_tree(args.root, args.keep, args.backup_dir or None)
    elif args.action == "declick":
        from .dataset_repair import declick_tree

        result = declick_tree(args.root, args.fade_ms, args.backup_dir or None)
    elif args.action == "qc":
        if getattr(args, "events", False):
            from .. import rpc

            result = qc(args.root, on_progress=lambda step, total: rpc.emit_progress("qc", step, total))
            rpc.emit("result", data=result)
        else:
            result = qc(args.root)
    elif args.action == "slice":
        result = slice_tree(args.root, args.min_seconds, args.max_seconds, args.min_interval_ms)
    elif args.action == "finalize":
        from .dataset_finalize import finalize_tree

        result = finalize_tree(args.root)
    elif args.action == "separate":
        from .dataset_separate import separate_tree

        result = separate_tree(args.root)
    elif args.action == "convert":
        from .dataset_convert import convert_tree

        result = convert_tree(args.root)
    elif args.action == "resample":
        result = resample_tree(args.root, args.sample_rate)
    else:
        result = assert_thresholds(qc(args.root))

    if args.out:
        write_report(result, args.out)
    import sys

    # --events 模式下结果已通过 rpc.emit("result") 输出，这里不再打印裸 JSON
    if not getattr(args, "events", False):
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    passed = True
    if isinstance(result, dict):
        assertions = result.get("assertions")
        if isinstance(assertions, dict):
            passed = bool(assertions.get("passed", True))
        else:
            passed = bool(result.get("passed", True))
    if args.action in {"qc", "assert"} and not passed:
        return exit_codes.INPUT
    return exit_codes.OK
