"""finalize：项目里只保留最终的干声片段。"""

import shutil
import tempfile
from pathlib import Path

import numpy as np

from svc_bridge.audio import write_wav
from svc_bridge.commands.dataset_finalize import finalize_tree


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="svcfin-"))


def test_keeps_only_final_clips_and_cleans_internal_dirs() -> None:
    root = _dir()
    try:
        spk = root / "spk"
        spk.mkdir()
        write_wav(spk / "clip_001.wav", np.zeros(44100, dtype=np.float32), 44100, 1)
        write_wav(spk / "long.wav", np.zeros(44100 * 20, dtype=np.float32), 44100, 1)
        write_wav(spk / "wrong_sr.wav", np.zeros(4800, dtype=np.float32), 48000, 1)
        (spk / "raw.mp3").write_bytes(b"ID3")
        internal = root / "_original" / "spk"
        internal.mkdir(parents=True)
        (internal / "raw.mp3").write_bytes(b"ID3")
        (root.parent / "quarantine").mkdir(exist_ok=True)

        result = finalize_tree(str(root))
        assert result["kept"] == ["spk/clip_001.wav"]
        assert not (spk / "long.wav").exists()
        assert not (spk / "wrong_sr.wav").exists()
        assert not (spk / "raw.mp3").exists()
        assert not (root / "_original").exists()
        assert result["keptCount"] == 1
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
