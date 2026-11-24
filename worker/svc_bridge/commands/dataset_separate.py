"""人声分离：优先 UVR5 MDX23C（`vocal_mix.separate_song`），失败再回退 Demucs。

so-vits-svc 本身不含分离能力，官方做法就是先用 UVR5 分离再训练；这里内置
MDX23C 权重（随安装包分发）与 Demucs 兜底，导入后自动跑，用户不需要额外装工具。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

WAV_EXTS = {".wav", ".wave"}
AUDIO_EXTS = WAV_EXTS | {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}


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


def separate_tree(root: str, device: str = "", model: str = "htdemucs") -> Dict[str, Any]:
    base = Path(root)
    torch_home = _prepare_torch_home()
    started = time.time()

    # 优先走 MDX23C；separate_song 内部已实现「MDX 失败自动回退 Demucs」
    from .vocal_mix import separate_song

    separated: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    backup_root = base / "_original"
    engines: Dict[str, int] = {}

    for spk in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for src in sorted(p for p in spk.iterdir() if p.is_file()):
            ext = src.suffix.lower()
            if ext not in AUDIO_EXTS and ext not in WAV_EXTS:
                continue
            if src.stem.endswith(".vocals"):
                continue
            dest = spk / f"{src.stem}.vocals.wav"
            if dest.exists():
                continue
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    result = separate_song(src, Path(tmp), device=device, model=model)
                    vocals = Path(str(result.get("vocals") or ""))
                    if not vocals.is_file():
                        raise RuntimeError(f"分离结果缺失：{result}")
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
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"relPath": f"{spk.name}/{src.name}", "error": str(exc)})

    return {
        "schema": "svcstudio.dataset.separate/1",
        "ok": not failed,
        "model": model,
        "device": device or _pick_device(device),
        "engines": engines,
        "offlineWeights": str(torch_home) if torch_home else None,
        "seconds": round(time.time() - started, 1),
        "separated": separated,
        "failed": failed,
    }
