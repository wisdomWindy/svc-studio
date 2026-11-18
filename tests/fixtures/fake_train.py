"""训练编排测试用的假 train.py。

模式来自环境变量 SVCSTUDIO_FAKE_MODE（默认 ok），因为真实调用时 argv 里是
`-c config -m model`，不能占用 argv[1]。
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    mode = os.environ.get("SVCSTUDIO_FAKE_MODE", "ok")
    if mode == "ok":
        for step in range(1, 131):
            losses = [1.0 + step / 1000.0, 2.0, 0.5, 1.5, 0.9]
            print(
                f"Losses: [{', '.join(str(x) for x in losses)}], step: {step}, lr: 0.0001, "
                f"reference_loss: 5.9",
                flush=True,
            )
            time.sleep(0.002)
        return 0

    if mode == "oom":
        print("Losses: [1.0, 2.0], step: 1, lr: 0.0001", flush=True)
        print("RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB", flush=True)
        return 1

    if mode == "nan":
        print("Losses: [nan, 2.0], step: 3, lr: 0.0001", flush=True)
        return 1

    if mode == "slow":
        print("Train Epoch: 1 [0%]", flush=True)
        time.sleep(30)
        return 0

    print(f"unknown mode {mode}", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
