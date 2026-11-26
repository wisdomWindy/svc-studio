"""分离后处理的音质回归：去爆音、去直流、切点选择。"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset
from svc_bridge.commands.dataset_repair import declick_tree


def tone(path: Path, seconds: float, amplitude: float = 0.5, sample_rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * sample_rate)
    samples = array.array("h")
    for i in range(total):
        samples.append(int(32767 * amplitude * math.sin(2 * math.pi * 440.0 * i / sample_rate)))
    audio.write_wav(path, samples, sample_rate, 1)
    return path


@pytest.fixture()
def raw_root(work: Path) -> Path:
    root = work / "dataset_raw"
    (root / "spk").mkdir(parents=True)
    return root


def test_fade_edges_zeroes_both_ends_and_keeps_interior() -> None:
    samples = array.array("h", [16000] * 4410)  # 100ms 恒定幅度：硬切的极端情况
    out = audio.fade_edges(samples, 44100, fade_ms=8.0)

    assert len(out) == len(samples), "淡入淡出不能改变长度"
    assert abs(out[0]) <= 1, "首样本必须被压到 0 附近"
    assert abs(out[-1]) <= 1, "末样本必须被压到 0 附近"
    assert out[2205] == 16000, "中间部分必须原样不动"
    # 淡入是单调上升的，不能有跳变
    assert all(out[i] <= out[i + 1] + 1 for i in range(0, 300))


def test_fade_edges_is_noop_for_single_sample() -> None:
    samples = array.array("h", [12345])
    assert audio.fade_edges(samples, 44100, fade_ms=8.0) is samples


def test_remove_dc_centers_offset_signal() -> None:
    samples = array.array("h", [1000, 1100, 900, 1000])
    centered = audio.remove_dc(samples)
    mean = sum(centered) / len(centered)
    assert abs(mean) < 1.0, f"去直流后均值应接近 0，实际 {mean}"


def test_remove_dc_ignores_sub_lsb_offset() -> None:
    samples = array.array("h", [0, 1, 0])  # 均值 0.33 LSB，属于可忽略偏置
    assert audio.remove_dc(samples) is samples


def test_find_quietest_cut_prefers_the_quiet_frame() -> None:
    frames = [0.5] * 20
    frames[12] = 0.001  # 只有这一帧安静
    idx, sample = audio.find_quietest_cut(frames, 0, 5, 18, frame_len=441)
    assert idx == 12
    assert sample == 12 * 441


def test_find_quietest_cut_falls_back_to_lowest_energy() -> None:
    frames = [0.9, 0.8, 0.01, 0.7, 0.6]  # 没有任何一帧低于静音阈值
    idx, _ = audio.find_quietest_cut(frames, 0, 0, 5, frame_len=100)
    assert idx == 2, "没有静音时应切在能量最低处，而不是固定时长硬切"


def test_slice_chunks_have_faded_edges(raw_root: Path) -> None:
    long = tone(raw_root / "spk" / "long.wav", 22.0)
    dataset.slice_tree(str(raw_root), 5.0, 15.0)

    chunks = sorted((raw_root / "spk").glob("long_*.wav"))
    assert chunks, "应当切出分片"
    for chunk in chunks:
        wav = audio.read_wav(chunk)
        assert abs(int(wav.samples[0])) <= 1, f"{chunk.name} 首样本未淡入：{wav.samples[0]}"
        assert abs(int(wav.samples[-1])) <= 1, f"{chunk.name} 末样本未淡出：{wav.samples[-1]}"
        assert wav.seconds >= 0.3
    assert not long.exists()


def test_declick_repairs_existing_clip_and_is_idempotent(work: Path) -> None:
    root = work / "dataset_raw"
    target = root / "spk" / "clip_001.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    # 中途硬切：首尾都在大振幅处（模拟没有淡入淡出的旧分片）
    samples = array.array("h", [20000] * 2205 + [int(20000 * 0.9)] * 2205)
    audio.write_wav(target, samples, 44100, 1)

    first = declick_tree(str(root), fade_ms=8.0, backup_dir=str(work / "backup"))
    assert first["changedCount"] == 1
    fixed = audio.read_wav(target)
    assert abs(int(fixed.samples[0])) <= 1
    assert abs(int(fixed.samples[-1])) <= 1
    assert fixed.frames == len(samples), "时长不能变"
    assert (work / "backup" / "spk" / "clip_001.wav").is_file(), "必须留下备份"

    second = declick_tree(str(root), fade_ms=8.0)
    assert second["changedCount"] == 0, "重复执行不应再改动文件"
    assert second["skippedCount"] == 1


def test_declick_skips_stereo(work: Path) -> None:
    root = work / "dataset_raw"
    target = root / "spk" / "st.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    audio.write_wav(target, array.array("h", [1000, 1000] * 100), 44100, 2)
    result = declick_tree(str(root))
    assert result["changedCount"] == 0
    assert result["skipped"][0]["reason"].startswith("不是单声道")
