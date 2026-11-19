import tempfile
from pathlib import Path

import shutil

import numpy as np

from svc_bridge import audio
from svc_bridge.commands import vocal_mix


def _tone(path: Path, seconds: float, amp: float) -> Path:
    sr = 44100
    n = int(seconds * sr)
    data = (amp * 32767 * np.sin(2 * np.pi * 440 * np.arange(n) / sr)).astype(np.int16)
    audio.write_wav(path, data, sr, 1)
    return path


def test_mix_keeps_accompaniment_length() -> None:
    tmp_path = Path(tempfile.mkdtemp(prefix='svcmix-'))
    vocal = _tone(tmp_path / "v.wav", 3.0, 0.3)
    accomp = _tone(tmp_path / "a.wav", 5.0, 0.3)
    out = tmp_path / "mix.wav"
    report = vocal_mix.mix_tracks(vocal, accomp, out)
    assert out.is_file()
    assert abs(report["seconds"] - 5.0) < 0.05
    wav = audio.read_wav(out)
    assert wav.sample_rate == 44100
    peak = np.max(np.abs(np.asarray(wav.samples, dtype=np.int16)))
    assert peak <= 32767
    shutil.rmtree(tmp_path, ignore_errors=True)
