"""人声分离：用 Demucs（htdemucs）把伴奏/背景音去掉，只保留人声。

so-vits-svc 本身不含分离能力，官方做法是先用 UVR5 分离再训练；
这里内置 Demucs，导入后自动跑，用户不需要额外装工具。

权重放在 `<运行时>/models/torch/hub/checkpoints/`，离线可加载（约 80MB）。
"""

from __future__ import annotations

import os
import shutil
import sys
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
    try:
        from demucs.api import Separator
        from demucs.audio import save_audio
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "svcstudio.dataset.separate/1",
            "ok": False,
            "error": f"Demucs 未安装：{exc}",
            "separated": [],
            "failed": [],
        }

    target_device = _pick_device(device)
    try:
        separator = Separator(model=model, device=target_device, progress=False)
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "svcstudio.dataset.separate/1",
            "ok": False,
            "error": f"加载分离模型失败：{exc}",
            "separated": [],
            "failed": [],
        }

    separated: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    backup_root = base / "_original"

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
                origin, stems = separator.separate_audio_file(str(src))
                save_audio(stems["vocals"], str(dest), separator.samplerate)
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
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"relPath": f"{spk.name}/{src.name}", "error": str(exc)})

    return {
        "schema": "svcstudio.dataset.separate/1",
        "ok": not failed,
        "model": model,
        "device": target_device,
        "offlineWeights": str(torch_home) if torch_home else None,
        "seconds": round(time.time() - started, 1),
        "separated": separated,
        "failed": failed,
    }
