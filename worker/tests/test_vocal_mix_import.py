from pathlib import Path
import shutil
import tempfile

import numpy as np
import pytest

from svc_bridge import audio
from svc_bridge.commands import infer, vocal_mix


def test_infer_module_exposes_vocal_mix() -> None:
    # 回归：曾经只写了调用没写 import，异常被吞掉后退化成整段转换（伴奏被克隆）
    assert infer.vocal_mix is vocal_mix


def test_mix_reads_flac_vocal_stem() -> None:
    sf = pytest.importorskip("soundfile")
    work = Path(tempfile.mkdtemp(prefix="svcmixflac-"))
    try:
        vocal = work / "v.flac"
        accomp = work / "a.wav"
        sr = 44100
        n = int(2.0 * sr)
        tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / sr)).astype(np.float32)
        sf.write(str(vocal), tone, sr, format="FLAC")
        audio.write_wav(accomp, (tone * 32767).astype(np.int16), sr, 1)

        out = work / "mix.wav"
        report = vocal_mix.mix_tracks(vocal, accomp, out)
        assert out.is_file()
        assert abs(report["seconds"] - 2.0) < 0.05
    finally:
        shutil.rmtree(work, ignore_errors=True)
