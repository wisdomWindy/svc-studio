"""检查内置运行时的 GPU 是否真的可用（不只是 is_available 标志）。

输出单行 JSON（纯 ASCII），由 setup.ps1 解析后展示。
"""

import json
import sys
import time


def main() -> int:
    result = {"ok": False, "cuda": False}
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"torch import failed: {exc}"
        print(json.dumps(result, ensure_ascii=True))
        return 0

    result["torch"] = torch.__version__
    result["cuda_built"] = torch.version.cuda

    if not torch.cuda.is_available():
        result["reason"] = "torch.cuda.is_available() == False"
        print(json.dumps(result, ensure_ascii=True))
        return 0

    try:
        props = torch.cuda.get_device_properties(0)
        # 真发一次 kernel：只有 is_available 为真但 kernel 不可用时才会暴露问题
        x = torch.randn(512, 512, device="cuda")
        x @ x
        torch.cuda.synchronize()
        started = time.perf_counter()
        for _ in range(5):
            x @ x
        torch.cuda.synchronize()
        elapsed = (time.perf_counter() - started) * 1000 / 5

        result.update(
            ok=True,
            cuda=True,
            device=torch.cuda.get_device_name(0),
            vram_mb=int(props.total_memory / 1048576),
            sm=f"{props.major}.{props.minor}",
            matmul_ms=round(elapsed, 2),
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
