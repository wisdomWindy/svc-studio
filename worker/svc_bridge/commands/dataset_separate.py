"""人声分离：优先 UVR5 MDX23C（`vocal_mix.separate_song`），失败再回退 Demucs。

so-vits-svc 本身不含分离能力，官方做法就是先用 UVR5 分离再训练；这里内置
MDX23C 权重（随安装包分发）与 Demucs 兜底，导入后自动跑，用户不需要额外装工具。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import quality_metrics, source_gate

WAV_EXTS = {".wav", ".wave"}
AUDIO_EXTS = WAV_EXTS | {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}

# 单段分离上限：超过就先切段。
# 41 分钟整段交给 RoFormer 会为整条轨道累积中间数组，把主机内存打满（实测可用 3GB 时整机换页），
# 而且中间没有任何进度。切段后内存按段回收，进度也能逐段上报。
MAX_SEPARATE_SECONDS = 600.0


def _prepare_torch_home() -> Optional[Path]:
    """把 TORCH_HOME 指向内置运行时里的缓存目录，避免联网拉权重。"""
    runtime_root = Path(sys.executable).parent
    candidate = runtime_root / "models" / "torch"
    if (candidate / "hub" / "checkpoints").is_dir():
        os.environ.setdefault("TORCH_HOME", str(candidate))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        return candidate
    return None


def _pick_device(requested: str = "") -> str:
    if requested:
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _ensure_pcm16(path: Path) -> None:
    """把分离结果就地转成 16-bit PCM。

    MDX23C 输出的是 32-bit float WAV，而下游切片用的 `read_wav` 只支持 16-bit PCM，
    不转的话「分离成功、切片全失败」。Demucs 本来就是 PCM_16，这里会直接跳过。
    声道数与采样率保持不变（立体声要留给切片自己做降混）。
    """
    try:
        import soundfile as sf
    except Exception:  # noqa: BLE001 - 缺 soundfile 时退回 ffmpeg
        sf = None  # type: ignore[assignment]

    if sf is not None:
        try:
            if sf.info(str(path)).subtype == "PCM_16":
                return
            import numpy as np

            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            tmp = path.with_name(path.stem + ".pcm16.wav")
            sf.write(str(tmp), np.clip(data, -1.0, 1.0), sr, subtype="PCM_16")
            tmp.replace(path)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[separate] 转 16-bit 失败，尝试 ffmpeg：{exc}", file=sys.stderr)

    from .dataset_convert import _convert_one, find_ffmpeg

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("无法把分离结果转成 16-bit PCM：既没有 soundfile 也没有 ffmpeg")
    tmp = path.with_name(path.stem + ".pcm16.wav")
    _convert_one(ffmpeg, path, tmp)
    tmp.replace(path)


def _original_for(base: Path, spk: Path, src: Path) -> Path:
    """优先返回 `_original/<说话人>/` 里的原始文件（通常是立体声）。

    dataset_raw 里放的是 44.1k 单声道副本（QC 与上游都按这个前提），但分离模型
    是按立体声训练的，用单声道会丢掉区分人声/伴奏的空间信息。原始文件在导入时
    被保留在 `_original/` 下，这里按同名 stem 找回来；找不到就用单声道副本。
    """
    backup_dir = base / "_original" / spk.name
    if backup_dir.is_dir():
        candidates = [
            c
            for c in sorted(backup_dir.iterdir())
            if c.is_file() and c.stem == src.stem and c.suffix.lower() in AUDIO_EXTS
        ]
        # 同 stem 可能同时存在「原始 mp3」和「分离后挪进来的单声道 wav」：优先非 wav 的原件
        non_wav = [c for c in candidates if c.suffix.lower() not in {".wav", ".wave"}]
        if non_wav:
            return non_wav[0]
        if candidates:
            return candidates[0]
    return src


def _ffmpeg_for_split() -> Optional[str]:
    """找 ffmpeg（切段用）：复用 source_gate 的解析顺序，找不到就返回 None（不切段）。"""
    try:
        return source_gate._ffmpeg()
    except Exception:  # noqa: BLE001
        return None


def _probe_seconds(ffmpeg: Optional[str], src: Path) -> float:
    """快速取时长：用 ffmpeg 的 Duration 行，不解码整段（41 分钟素材解码一次要十几秒）。"""
    if not ffmpeg:
        return 0.0
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(src)],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
        if not match:
            return 0.0
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:  # noqa: BLE001
        return 0.0


def _split_long_source(ffmpeg: str, src: Path, part_seconds: float) -> List[Path]:
    """把超长素材按固定时长切成若干段（无损 PCM），返回切好的文件。

    为什么要切：整段 41 分钟的音频交给 RoFormer，需要为整条轨道累积中间数组，
    主机内存会被打满（实测可用内存只剩 3GB 时整机开始疯狂换页），而且中间没有任何进度，
    用户只能干等。切成 ≤part_seconds 的段后，内存按段回收、进度也能逐段上报。
    """
    duration = _probe_seconds(ffmpeg, src)
    if duration <= part_seconds or part_seconds <= 0:
        return []
    parts: List[Path] = []
    index = 1
    offset = 0.0
    while offset < duration - 1.0:
        part = src.with_name(f"{src.stem}.part{index:02d}.wav")
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{offset:.3f}", "-t", f"{part_seconds:.3f}", "-i", str(src),
                "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(part),
            ],
            check=True,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        parts.append(part)
        offset += part_seconds
        index += 1
    return parts


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_separate_lock(base: Path) -> Path:
    """跨进程互斥：同一个数据集同时只允许一个分离进程。

    应用内的 DatasetLock 只在单个进程内有效；实测出现过同一 app 实例下两个
    `dataset separate` 同时跑、抢同一块 GPU 的情况（两个进程各占 ~7GB 内存、
    GPU 100%，41 分钟的文件跑了 30 分钟还没完）。这里用「锁文件 + PID 存活检查」
    做第二道闸：持有者进程已死则视为陈旧锁并接管。

    返回锁文件路径；被占用时抛 RuntimeError。
    """
    lock = base.parent / ".svcstudio" / "separate.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            holder = int((lock.read_text(encoding="utf-8") or "0").strip() or 0)
        except Exception:  # noqa: BLE001
            holder = 0
        if holder and holder != os.getpid() and _pid_alive(holder):
            raise RuntimeError(
                f"数据集正在被另一个分离进程占用（PID {holder}）。"
                "请等它结束；如果确认该进程已异常退出，删除锁文件后重试："
                f"{lock}"
            )
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def _release_separate_lock(lock: Optional[Path]) -> None:
    if lock is None:
        return
    try:
        if lock.is_file() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink()
    except Exception:  # noqa: BLE001
        pass


def separate_tree(
    root: str,
    device: str = "",
    model: str = "htdemucs",
    drop_rejected: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    base = Path(root)
    # 跨进程互斥：并发分离会把 GPU/内存打满，实测两个进程各 7GB、GPU 100% 互相拖慢
    try:
        lock = acquire_separate_lock(base)
    except RuntimeError as exc:
        # 不抛异常：调用方按 busy 处理并给出可读错误（SVC-3010）
        return {
            "schema": "svcstudio.dataset.separate/1",
            "ok": False,
            "busy": True,
            "error": str(exc),
            "separated": [],
            "skipped": [],
            "failed": [],
            "engines": {},
        }
    try:
        return _separate_tree_locked(base, device, model, drop_rejected, on_progress)
    finally:
        _release_separate_lock(lock)


def _separate_tree_locked(
    base: Path,
    device: str,
    model: str,
    drop_rejected: bool,
    on_progress: Optional[Callable[[int, int], None]],
) -> Dict[str, Any]:
    torch_home = _prepare_torch_home()
    started = time.time()

    # 优先走 MDX23C；separate_song 内部已实现「MDX 失败自动回退 Demucs」
    from .vocal_mix import separate_song

    separated: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    # 分离前体检：明显不可用的源文件直接跳过（省一次 GPU），并如实记录原因
    skipped: List[Dict[str, Any]] = []
    backup_root = base / "_original"
    engines: Dict[str, int] = {}

    # 先收集待处理清单：进度需要「总数」，不能边遍历边算
    jobs: List[tuple] = []
    split_reports: List[Dict[str, Any]] = []
    ffmpeg = _ffmpeg_for_split()
    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for src in sorted(p for p in spk.iterdir() if p.is_file()):
            ext = src.suffix.lower()
            if ext not in AUDIO_EXTS and ext not in WAV_EXTS:
                continue
            # 跳过所有已分离产物：`x.vocals.wav`（整段干声）与 `x.vocals_001.wav`（切片）。
            # 否则对已经处理过的数据集再点一次「自动分离」，会把切片当成原曲重跑一遍。
            if ".vocals" in src.stem:
                continue
            # 跳过之前切出来的分段：它们由原文件派生，避免二次切分
            if ".part" in src.stem:
                continue
            dest = spk / f"{src.stem}.vocals.wav"
            if dest.exists():
                continue
            # 超长素材先切段：整段分离会打满内存且没有中间进度
            seconds = _probe_seconds(ffmpeg, src)
            if ffmpeg and seconds > MAX_SEPARATE_SECONDS:
                try:
                    parts = _split_long_source(ffmpeg, src, MAX_SEPARATE_SECONDS)
                except Exception as exc:  # noqa: BLE001
                    parts = []
                    failed.append({"relPath": f"{spk.name}/{src.name}", "reason": f"切段失败: {exc}"})
                if parts:
                    backup_root.mkdir(parents=True, exist_ok=True)
                    backup = backup_root / spk.name
                    backup.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(src), str(backup / src.name))
                    except Exception:  # noqa: BLE001
                        src.unlink(missing_ok=True)
                    split_reports.append(
                        {
                            "relPath": f"{spk.name}/{src.name}",
                            "seconds": round(seconds, 1),
                            "parts": len(parts),
                        }
                    )
                    for part in parts:
                        jobs.append((spk, part, spk / f"{part.stem}.vocals.wav"))
                    continue
            jobs.append((spk, src, dest))

    total = len(jobs)
    if on_progress is not None:
        on_progress(0, total)

    for done, (spk, src, dest) in enumerate(jobs, 1):
        if True:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    sep_src = _original_for(base, spk, src)
                    metrics = source_gate.analyze(sep_src)
                    reason = source_gate.gate_reason(metrics)
                    if reason:
                        skipped.append(
                            {
                                "relPath": f"{spk.name}/{src.name}",
                                "reason": reason,
                                "deleted": drop_rejected,
                                **{k: v for k, v in metrics.items() if k != "readable"},
                            }
                        )
                        if drop_rejected:
                            src.unlink(missing_ok=True)
                        continue
                    result = separate_song(sep_src, Path(tmp), device=device, model=model)
                    vocals = Path(str(result.get("vocals") or ""))
                    if not vocals.is_file():
                        raise RuntimeError(f"分离结果缺失：{result}")
                    _ensure_pcm16(vocals)
                    quiet = source_gate.vocal_too_quiet(vocals)
                    if quiet is not None:
                        skipped.append(
                            {
                                "relPath": f"{spk.name}/{src.name}",
                                "reason": f"分离后干声几乎为空（{quiet:.1f} dBFS）",
                                "deleted": drop_rejected,
                            }
                        )
                        if drop_rejected:
                            src.unlink(missing_ok=True)
                        continue
                    # P1：分离质量指标（只报告，不参与删除）
                    quality = quality_metrics.measure(
                        vocals, Path(str(result.get("accompaniment") or "")) if result.get("accompaniment") else None
                    )
                    shutil.copyfile(vocals, dest)
                    engine = str(result.get("engine") or "unknown")
                    engines[engine] = engines.get(engine, 0) + 1
                backup_dir = backup_root / spk.name
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / src.name
                if not backup.exists():
                    shutil.move(str(src), str(backup))
                else:
                    src.unlink(missing_ok=True)
                separated.append(
                    {
                        "relPath": f"{spk.name}/{src.name}",
                        "output": f"{spk.name}/{dest.name}",
                        "engine": engine,
                        "quality": quality,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"relPath": f"{spk.name}/{src.name}", "error": str(exc)})
            finally:
                # continue 也会触发 finally：确保「跳过/失败」的文件同样推进进度，
                # 否则界面会停在 已分离 0/N。
                if on_progress is not None:
                    on_progress(done, total)

    return {
        "schema": "svcstudio.dataset.separate/1",
        "ok": not failed,
        "model": model,
        "device": device or _pick_device(device),
        "engines": engines,
        "offlineWeights": str(torch_home) if torch_home else None,
        "seconds": round(time.time() - started, 1),
        "separated": separated,
        # 分离质量汇总：只报告，不参与删除（阈值待标定后由使用方决定）
        "quality": quality_metrics.summarize([s.get("quality") or {} for s in separated]),
        "skipped": skipped,
        "failed": failed,
        # 超长素材被切段处理的记录（原文件已移入 _original/）
        "splitSources": split_reports,
        "maxSeparateSeconds": MAX_SEPARATE_SECONDS,
    }
