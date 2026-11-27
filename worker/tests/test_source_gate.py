"""源文件体检与筛选：只拦客观不可用，绝不按内容裁量。

阈值依据实测：正常流行歌削波 0、静音中位 0.5%、时长 15s–4.5min。
"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from svc_bridge import audio
from svc_bridge.commands import dataset_separate, source_gate


def _write(path: Path, seconds: float, kind: str = "tone") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * 44100)
    samples = array.array("h")
    for i in range(total):
        if kind == "silence":
            samples.append(0)
        elif kind == "clipped":
            samples.append(32767 if (i // 100) % 2 == 0 else -32768)
        else:
            samples.append(int(0.4 * 32767 * math.sin(2 * math.pi * 220 * i / 44100)))
    audio.write_wav(path, samples, 44100, 1)
    return path


def test_normal_song_passes(work: Path) -> None:
    metrics = source_gate.analyze(_write(work / "normal.wav", 20))
    assert metrics["readable"] is True
    assert metrics["seconds"] == 20.0
    assert source_gate.gate_reason(metrics) is None


def test_silent_file_is_blocked(work: Path) -> None:
    metrics = source_gate.analyze(_write(work / "silent.wav", 20, kind="silence"))
    assert metrics["silenceRatio"] >= 0.9
    assert "静音" in (source_gate.gate_reason(metrics) or "")


def test_clipped_file_is_blocked(work: Path) -> None:
    metrics = source_gate.analyze(_write(work / "clipped.wav", 10, kind="clipped"))
    assert metrics["clipRatio"] > 0.001
    assert "削波" in (source_gate.gate_reason(metrics) or "")


def test_too_short_is_blocked(work: Path) -> None:
    metrics = source_gate.analyze(_write(work / "short.wav", 2))
    assert "时长过短" in (source_gate.gate_reason(metrics) or "")


def test_too_long_is_blocked(work: Path) -> None:
    metrics = source_gate.analyze(_write(work / "long.wav", 6))
    metrics["seconds"] = 1200.0
    assert "时长过长" in (source_gate.gate_reason(metrics) or "")


def test_unreadable_is_blocked(work: Path, monkeypatch) -> None:
    """有解码器但文件真的坏 → 判「解码失败」并拦截。"""
    ffmpeg = Path(r"D:\program\svcstudio\SVC Studio\resources\bin\ffmpeg.exe")
    if not ffmpeg.is_file():
        pytest.skip("需要真实 ffmpeg 才能验证解码失败")
    monkeypatch.setattr(source_gate, "_ffmpeg", lambda: str(ffmpeg))
    bad = work / "broken.mp3"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not audio at all")
    metrics = source_gate.analyze(bad)
    assert metrics["readable"] is False
    assert metrics.get("noDecoder") is False
    assert source_gate.gate_reason(metrics) == "解码失败"


def test_bandwidth_is_reported_but_does_not_block(work: Path) -> None:
    """转码导致的高频缺失只做提示：用户素材普遍如此，拦掉会把数据集清空。"""
    metrics = source_gate.analyze(_write(work / "tone.wav", 10))
    assert "rolloffHz" in metrics
    assert source_gate.gate_reason(metrics) is None


def test_separate_tree_skips_hopeless_sources_and_reports_them(work: Path) -> None:
    """链路层：坏文件不进分离，且出现在 skipped 里（不是静默消失）。"""
    raw = work / "dataset_raw"
    spk = raw / "spk"
    _write(spk / "good.wav", 10)
    _write(spk / "silent.wav", 10, kind="silence")

    report = dataset_separate.separate_tree(str(raw))

    skipped = {s["relPath"]: s for s in report["skipped"]}
    assert "spk/silent.wav" in skipped, f"静音文件应被跳过，实际 skipped={report['skipped']}"
    assert "静音" in skipped["spk/silent.wav"]["reason"]
    # 默认直接删除不合格源文件（只保留合格音频），不产生干声产物
    assert not (spk / "silent.wav").exists()
    assert skipped["spk/silent.wav"]["deleted"] is True
    assert not (spk / "silent.vocals.wav").exists()
    assert all(s["relPath"] != "spk/silent.wav" for s in report["separated"])


def test_separate_tree_can_keep_rejected(work: Path) -> None:
    """排障时可要求保留不合格文件（--keep-rejected）。"""
    raw = work / "dataset_raw"
    spk = raw / "spk"
    _write(spk / "silent.wav", 10, kind="silence")

    report = dataset_separate.separate_tree(str(raw), drop_rejected=False)

    assert report["skipped"][0]["deleted"] is False
    assert (spk / "silent.wav").exists()

def test_missing_decoder_does_not_condemn_the_file(work: Path, monkeypatch) -> None:
    """环境缺 ffmpeg 时不能把文件判成坏文件（否则一个配置问题就删光数据集）。"""
    bad = work / "song.mp3"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not really audio")

    monkeypatch.setattr(source_gate, "_ffmpeg", lambda: None)
    metrics = source_gate.analyze(bad)

    assert metrics["readable"] is False
    assert metrics["noDecoder"] is True
    assert source_gate.gate_reason(metrics) is None, "缺解码器不应拦截文件"

    # 链路层：不会被删除，交给分离流程去报错
    raw = work / "dataset_raw"
    spk = raw / "spk"
    spk.mkdir(parents=True, exist_ok=True)
    bad2 = spk / "song.mp3"
    bad2.write_bytes(b"not really audio")
    report = dataset_separate.separate_tree(str(raw))
    assert bad2.exists(), "缺解码器时不允许删除源文件"
    assert all("song.mp3" not in s["relPath"] for s in report["skipped"])
