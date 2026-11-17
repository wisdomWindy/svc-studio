"""导入后自动转码/切片/重采样的回归测试。

不依赖真实 ffmpeg：只验证判定逻辑与目录约定；真正转码在 E2E 里用 ffmpeg 跑。
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np

from svc_bridge.audio import write_wav
from svc_bridge.commands.dataset import scan
from svc_bridge.commands.dataset_convert import needs_convert


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="svcconv-"))


def test_needs_convert_for_non_wav() -> None:
    root = _dir()
    try:
        mp3 = root / "a.mp3"
        mp3.write_bytes(b"ID3fake")
        assert needs_convert(mp3) is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_needs_convert_false_for_44100_mono() -> None:
    root = _dir()
    try:
        wav = root / "a.wav"
        write_wav(wav, np.zeros(4410, dtype=np.float32), 44100, 1)
        assert needs_convert(wav) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_needs_convert_true_for_48k_stereo() -> None:
    root = _dir()
    try:
        wav = root / "b.wav"
        write_wav(wav, np.zeros(4800 * 2, dtype=np.float32), 48000, 2)
        assert needs_convert(wav) is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_scan_ignores_internal_dirs() -> None:
    root = _dir()
    try:
        spk = root / "spk"
        spk.mkdir()
        write_wav(spk / "a.wav", np.full(4410, 0.1, dtype=np.float32), 44100, 1)
        internal = root / "_original" / "spk"
        internal.mkdir(parents=True)
        (internal / "a.mp3").write_bytes(b"ID3fake")

        result = scan(str(root))
        assert result["speakers"] == ["spk"], "内部目录不应被当成说话人"
        assert result["blocking"] == [], f"不应有阻断：{result['blocking']}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
