from __future__ import annotations

import io
import json

from svc_bridge import rpc


def test_emit_writes_single_json_line(capsys) -> None:
    rpc.emit("progress", stage="preprocess", step=3, total=10)
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert out.count("\n") == 1
    payload = json.loads(out)
    assert payload["v"] == rpc.PROTOCOL_VERSION
    assert payload["kind"] == "progress"
    assert payload["step"] == 3


def test_emit_keeps_chinese_readable(capsys) -> None:
    rpc.emit_log("数据集校验通过")
    payload = json.loads(capsys.readouterr().out)
    assert payload["message"] == "数据集校验通过"


def test_read_control_parses_command(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('{"cmd":"cancel"}\n'))
    assert rpc.read_control() == {"cmd": "cancel"}


def test_read_control_returns_none_on_eof(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert rpc.read_control() is None


def test_read_control_marks_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not-json\n"))
    assert rpc.read_control() == {"cmd": "invalid"}


def test_ping_handshake(capsys) -> None:
    from svc_bridge.__main__ import main

    assert main(["ping"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "hello"
    assert payload["bridge"] == "0.1.0"
