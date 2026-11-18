"""静态声线混合的规则校验（W7-7）。

真正的权重混合需要 torch 加载多份模型；这里先把"能不能混"的规则做死：
- 2–8 个模型；
- 每个模型必须是单说话人（多说话人混合会串音）；
- 所有模型的 config 结构必须一致（同一 speech_encoder / 同维度）；
- 权重必须为正；凸组合会自动归一，线性组合原样使用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _issue(code: str, field: str, message: str, detail: str = "") -> Dict[str, str]:
    return {"code": code, "field": field, "message": message, "detail": detail}


def validate_mix(
    models: List[Dict[str, Any]],
    mode: str = "convex",
    weights: Optional[List[float]] = None,
) -> Dict[str, Any]:
    blocking: List[Dict[str, str]] = []

    if len(models) < 2 or len(models) > 8:
        blocking.append(_issue("SVC-4001", "models", "混合需要 2–8 个模型", str(len(models))))

    for index, model in enumerate(models):
        speakers = int(model.get("speakerCount") or 1)
        if speakers > 1:
            blocking.append(
                _issue(
                    "SVC-6001",
                    f"models[{index}]",
                    "只能混合单说话人模型",
                    f"{model.get('name')} 有 {speakers} 个说话人",
                )
            )
        if not model.get("path"):
            blocking.append(_issue("SVC-2001", f"models[{index}]", "模型缺少路径"))

    if models:
        keys = {(m.get("encoder"), m.get("sslDim"), m.get("ginChannels")) for m in models}
        if len(keys) > 1:
            blocking.append(
                _issue(
                    "SVC-6001",
                    "models",
                    "所有混合模型的 config 结构必须一致",
                    f"发现 {len(keys)} 种结构",
                )
            )

    if mode not in ("convex", "linear"):
        blocking.append(_issue("SVC-4001", "mode", "混合模式只支持 convex / linear", mode))

    weights = list(weights) if weights is not None else [1.0] * len(models)
    if len(weights) != len(models):
        blocking.append(
            _issue("SVC-4001", "weights", "权重数量必须与模型数量一致", f"{len(weights)} != {len(models)}")
        )
    if any(w <= 0 for w in weights):
        blocking.append(_issue("SVC-4001", "weights", "权重必须为正数"))

    effective: List[float] = []
    if not blocking:
        total = sum(weights)
        effective = [round(w / total, 6) for w in weights] if mode == "convex" else [
            round(w, 6) for w in weights
        ]

    return {
        "passed": not blocking,
        "blocking": blocking,
        "mode": mode,
        "weights": effective,
        "note": "凸组合会把权重归一到 1；线性组合保持原值（上游对两者行为不同）",
    }

def main(argv=None) -> int:
    """读 JSON 计划文件并输出校验结果（供 Rust 侧调用）。"""
    import argparse
    import json
    import sys

    from .. import exit_codes

    parser = argparse.ArgumentParser(prog="svc_bridge mix")
    parser.add_argument("--input", required=True, help="计划 JSON：{models:[],mode,weights}")
    args = parser.parse_args(argv)

    with open(args.input, encoding="utf-8-sig") as fh:
        plan = json.load(fh)

    result = validate_mix(
        plan.get("models") or [],
        mode=plan.get("mode") or "convex",
        weights=plan.get("weights"),
    )
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return exit_codes.OK if result["passed"] else exit_codes.USAGE