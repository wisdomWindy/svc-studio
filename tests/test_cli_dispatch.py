"""CLI 分发守卫：每个子命令都必须真的转到对应模块。

背景：`train`/`aux`/`model`/`infer`/`modeltool` 曾只注册了 argparse、没写分发，
CLI 直接返回 USAGE(2)。Rust 侧正是通过这些子命令拉起 worker 的，缺分发会让
训练/判定/推理在真实运行时报"退出码 2"且没有错误信息。本测试防止回归。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from svc_bridge import __main__ as cli  # noqa: E402
from svc_bridge.commands import (  # noqa: E402
    extras,
    infer_cli,
    model,
    modeltool,
    train,
)


@pytest.fixture()
def recorder(monkeypatch):
    calls = {}

    def make(name, ret=0):
        def fake(argv):
            calls[name] = list(argv)
            return ret

        return fake

    monkeypatch.setattr(train, "main", make("train"))
    monkeypatch.setattr(extras, "main", make("aux"))
    monkeypatch.setattr(model, "main", make("model"))
    monkeypatch.setattr(infer_cli, "main", make("infer"))
    monkeypatch.setattr(modeltool, "main", make("modeltool"))
    return calls


def test_train_dispatch_forwards_smoke(recorder):
    rc = cli.main(
        [
            "train",
            "--project",
            "P",
            "--upstream",
            "U",
            "--smoke",
            "--smoke-steps",
            "50",
        ]
    )
    assert rc == 0
    argv = recorder["train"]
    assert "--smoke" in argv
    assert argv[argv.index("--smoke-steps") + 1] == "50"
    assert argv[argv.index("--project") + 1] == "P"


def test_aux_dispatch(recorder):
    assert cli.main(["aux", "--project", "P", "--upstream", "U", "--kind", "cluster"]) == 0
    assert recorder["aux"][-1] == "cluster"


def test_model_dispatch(recorder):
    assert cli.main(["model", "check", "--model", "m.pth", "--config", "c.json"]) == 0
    assert recorder["model"][0] == "check"


def test_infer_dispatch_keeps_all_inputs(recorder):
    rc = cli.main(
        [
            "infer",
            "--project",
            "P",
            "--model",
            "m.pth",
            "--config",
            "c.json",
            "--params",
            "p.json",
            "--spk",
            "spk",
            "--inputs",
            "a.wav",
            "b.wav",
        ]
    )
    assert rc == 0
    argv = recorder["infer"]
    assert argv[argv.index("--inputs") + 1 :] == ["a.wav", "b.wav"]


def test_modeltool_dispatch_passes_half(recorder):
    assert (
        cli.main(
            [
                "modeltool",
                "compress",
                "--project",
                "P",
                "--upstream",
                "U",
                "--input",
                "i.pth",
                "--output",
                "o.pth",
                "--half",
            ]
        )
        == 0
    )
    assert recorder["modeltool"][0] == "compress"
    assert "--half" in recorder["modeltool"]
