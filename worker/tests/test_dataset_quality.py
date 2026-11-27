"""分离质量筛选与"分离结果转 16-bit"的回归测试。"""

from __future__ import annotations

import array
import math
import random
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands.dataset_quality import prune_quality_tree
from svc_bridge.commands.dataset_separate import _ensure_pcm16, _original_for
from svc_bridge.commands.vocal_mix import _load_roformer_config


def write_tone(
    path: Path, seconds: float = 4.0, tone: float = 0.5, noise: float = 0.001
) -> Path:
    """合成"有声段 + 静音段"的正弦，静音段里叠加均匀噪声。

    噪声越大、静音段越不安静，SNR 代理就越低——这与真实分离片段的行为一致
    （分离残留主要出现在原本的间隙里）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * 44100)
    half = 44100 // 2  # 0.5s 有声 / 0.5s 间隙交替
    rng = random.Random(42)
    samples = array.array("h")
    for i in range(total):
        voiced = (i // half) % 2 == 0
        v = (tone * math.sin(2 * math.pi * 220.0 * i / 44100) if voiced else 0.0) + rng.uniform(
            -noise, noise
        )
        samples.append(int(max(-1.0, min(1.0, v)) * 32767))
    audio.write_wav(path, samples, 44100, 1)
    return path


def test_prune_quality_drops_the_noisiest(work: Path) -> None:
    root = work / "dataset_raw"
    for i in range(5):
        write_tone(root / "spk" / f"clean_{i}.wav", noise=0.001)
    for i in range(5):
        write_tone(root / "spk" / f"noisy_{i}.wav", noise=0.25)

    result = prune_quality_tree(str(root), keep=0.5, backup_dir=str(work / "pruned"))

    assert result["totalFiles"] == 10
    assert result["droppedFiles"] == 5
    assert result["keptFiles"] == 5
    dropped = {d["relPath"] for d in result["dropped"]}
    assert all("noisy_" in d for d in dropped), f"应只剔除高底噪片段：{dropped}"
    assert result["snrThresholdDb"] is not None

    # 剔除的是移动而不是删除
    assert (work / "pruned" / "spk" / "noisy_0.wav").is_file()
    assert not (root / "spk" / "noisy_0.wav").exists()
    assert (root / "spk" / "clean_0.wav").is_file()


def test_prune_quality_keep_all_is_noop(work: Path) -> None:
    root = work / "dataset_raw"
    write_tone(root / "spk" / "a.wav")
    write_tone(root / "spk" / "b.wav", noise=0.3)
    result = prune_quality_tree(str(root), keep=1.0, backup_dir=str(work / "pruned"))
    assert result["droppedFiles"] == 0
    assert (root / "spk" / "a.wav").is_file() and (root / "spk" / "b.wav").is_file()


def test_prune_quality_reports_unreadable(work: Path) -> None:
    root = work / "dataset_raw"
    write_tone(root / "spk" / "ok.wav")
    bad = root / "spk" / "broken.wav"
    bad.write_bytes(b"not a wav at all")
    result = prune_quality_tree(str(root), keep=0.85, backup_dir=str(work / "pruned"))
    assert result["readableFiles"] == 1
    assert result["unreadable"] and result["unreadable"][0]["relPath"] == "broken.wav"
    assert bad.is_file(), "不可解码的文件不应被移走"


def test_ensure_pcm16_makes_float_wav_readable(work: Path) -> None:
    """MDX23C 输出 32-bit float，而 read_wav 只吃 16-bit：这一步是链路能否走通的关键。"""
    sf = pytest.importorskip("soundfile")
    import numpy as np

    path = work / "float_vocals.wav"
    data = (0.3 * np.sin(2 * np.pi * 440 * np.arange(44100) / 44100)).astype("float32")
    sf.write(str(path), np.stack([data, data], axis=1), 44100, subtype="FLOAT")

    with pytest.raises(Exception):
        audio.read_wav(path)  # 转换前确实读不了

    _ensure_pcm16(path)

    assert sf.info(str(path)).subtype == "PCM_16"
    wav = audio.read_wav(path)
    assert wav.sample_rate == 44100
    assert wav.channels == 2, "声道数必须保留，降混留给切片阶段"
    assert abs(wav.seconds - 1.0) < 0.01


def test_ensure_pcm16_skips_already_pcm16(work: Path) -> None:
    pytest.importorskip("soundfile")
    path = write_tone(work / "pcm.wav")
    before = path.stat().st_mtime_ns
    _ensure_pcm16(path)
    assert path.stat().st_mtime_ns == before, "已经是 PCM_16 时不应重写文件"


def test_original_for_prefers_backup_and_falls_back(work: Path) -> None:
    """分离优先用 _original/ 里的原始文件；没有就用 dataset_raw 里的单声道副本。"""
    root = work / "dataset_raw"
    spk = root / "spk"
    spk.mkdir(parents=True)
    mono = write_tone(spk / "song.wav")

    assert _original_for(root, spk, mono) == mono, "没有备份时应退回单声道副本"

    backup = root / "_original" / "spk"
    backup.mkdir(parents=True)
    original = write_tone(backup / "song.flac")
    assert _original_for(root, spk, mono) == original


def test_roformer_config_strips_unsafe_tuple_tag(work: Path) -> None:
    """UVR 的 roformer 配置带 !!python/tuple，safe_load 会拒绝，必须先剥掉标签。"""
    pytest.importorskip("yaml")
    cfg = work / "model_bs_roformer_ep_368_sdr_12.9628.yaml"
    cfg.write_text(
        "audio:\n"
        "  sample_rate: 44100\n"
        "model:\n"
        "  dim: 512\n"
        "  freqs_per_bands: !!python/tuple\n"
        "    - 2\n"
        "    - 4\n"
        "training:\n"
        "  instruments:\n"
        "    - Vocals\n"
        "  target_instrument: Vocals\n"
        "inference:\n"
        "  num_overlap: 4\n",
        encoding="utf-8",
    )
    raw, ns = _load_roformer_config(cfg)
    assert raw["model"]["freqs_per_bands"] == [2, 4]
    assert ns.audio.sample_rate == 44100
    assert ns.training.target_instrument == "Vocals"
    assert ns.inference.num_overlap == 4

def test_prune_quality_deletes_by_default(work: Path) -> None:
    """产品要求：只保留合格音频，不留备份副本。"""
    import array

    from svc_bridge import audio

    root = work / "dataset_raw"
    spk = root / "spk"
    spk.mkdir(parents=True)
    for i in range(4):
        amp = 300 if i == 0 else 12000  # 第一个最安静 → SNR 代理最差
        audio.write_wav(spk / f"f{i}.wav", array.array("h", [amp, -amp] * 44100), 44100, 1)

    result = prune_quality_tree(str(root), keep=0.5)  # 不传 backup_dir

    assert result["deletedFiles"] == 2
    assert result["backupDir"] is None
    assert result["droppedFiles"] == 2
    assert len(list(spk.glob("*.wav"))) == 2, "被剔除的文件必须真的从磁盘删除"
    assert not (work / "_pruned").exists(), "默认不应再产生备份目录"
    assert all(d.get("deleted") is True for d in result["dropped"])
