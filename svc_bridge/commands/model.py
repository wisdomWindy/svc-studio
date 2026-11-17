"""模型兼容性判定（7 步，附录 A.10）。

判定只依赖"结构"（键名 + 形状），不依赖训练来源。torch 是可选依赖：
- 有 torch：直接读 state_dict；
- 没有 torch：返回 `unknown` 并给出明确原因，不假装能判定。
测试通过注入 shapes 覆盖全部规则，不依赖权重文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REQUIRED_PREFIXES = ("enc_p.", "flow.", "dec.", "emb_g.", "ref_enc.")
ENCODER_BY_DIM = {
    256: ["vec256l9", "hubertsoft"],
    768: ["vec768l12", "dphubert", "wavlmbase+"],
    1024: ["whisper-ppg", "cnhubertlarge"],
    1280: ["whisper-ppg-large"],
}
ENCODER_LIMITS = {"whisper-ppg": 25.0, "whisper-ppg-large": 25.0}

Shape = Tuple[int, ...]


def normalize_state_dict(raw: Any) -> Tuple[Dict[str, Shape], List[str]]:
    """兼容三种封装：裸 state_dict、{"model": ...}、带 `module.` 前缀。"""
    notes: List[str] = []
    if isinstance(raw, dict) and "model" in raw and isinstance(raw["model"], dict):
        raw = raw["model"]
        notes.append("从 checkpoint['model'] 读取权重")
    if not isinstance(raw, dict):
        raise ValueError("state_dict 不是字典")

    out: Dict[str, Shape] = {}
    prefixed = sum(1 for k in raw if str(k).startswith("module."))
    if prefixed:
        notes.append(f"剥离 module. 前缀（{prefixed} 个键）")
    for key, value in raw.items():
        name = str(key)
        if name.startswith("module."):
            name = name[len("module.") :]
        shape = getattr(value, "shape", None)
        if shape is None:
            continue
        out[name] = tuple(int(d) for d in shape)
    return out, notes


def infer_encoder(ssl_dim: int) -> Tuple[Optional[str], List[str]]:
    candidates = ENCODER_BY_DIM.get(int(ssl_dim), [])
    if len(candidates) == 1:
        return candidates[0], [f"由 ssl_dim={ssl_dim} 推断编码器 {candidates[0]}"]
    if candidates:
        return candidates[0], [
            f"ssl_dim={ssl_dim} 对应多个编码器 {candidates}，先按 {candidates[0]} 推断处理"
        ]
    return None, [f"ssl_dim={ssl_dim} 没有已知编码器"]


def _ssl_dim_from_shapes(shapes: Dict[str, Shape]) -> Optional[int]:
    for key in ("enc_p.emb_phone.weight", "enc_p.emb_phone.bias"):
        shape = shapes.get(key)
        if shape and len(shape) == 1:
            return int(shape[0])
        if shape and len(shape) == 2:
            return int(shape[1])
    return None


def check_compat(
    config: Dict[str, Any],
    shapes: Dict[str, Shape],
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """返回 compat.json 结构；verdict ∈ ready|compressed|incompatible|unknown。"""
    notes = list(notes or [])
    model_cfg = config.get("model") if isinstance(config, dict) else None
    if not isinstance(model_cfg, dict):
        return {
            "verdict": "incompatible",
            "code": "SVC-4001",
            "message": "config.json 缺少 model 段",
            "missingKeys": [],
            "notes": notes,
        }

    missing: List[str] = []
    for prefix in REQUIRED_PREFIXES:
        if not any(k.startswith(prefix) for k in shapes):
            missing.append(prefix)

    encoder = model_cfg.get("speech_encoder")
    ssl_dim = model_cfg.get("ssl_dim")
    ssl_from_shape = _ssl_dim_from_shapes(shapes)

    if not encoder:
        dim = int(ssl_dim) if isinstance(ssl_dim, int) else (ssl_from_shape or 0)
        inferred, infer_notes = infer_encoder(dim)
        notes.extend(infer_notes)
        if not inferred:
            missing.append("model.speech_encoder")
        encoder = inferred

    if isinstance(ssl_dim, int) and ssl_from_shape and int(ssl_dim) != ssl_from_shape:
        return {
            "verdict": "incompatible",
            "code": "SVC-6001",
            "message": "config 的 ssl_dim 与权重形状不一致",
            "detail": f"config={ssl_dim}，权重={ssl_from_shape}",
            "missingKeys": missing,
            "notes": notes,
        }

    spk = config.get("spk")
    emb_rows = shapes.get("emb_g.weight", (0,))[0] if "emb_g.weight" in shapes else 0
    if isinstance(spk, dict) and spk and emb_rows and int(emb_rows) != len(spk):
        notes.append(f"emb_g 行数 {emb_rows} 与 spk 数量 {len(spk)} 不一致，可能映射错位")

    if missing:
        return {
            "verdict": "incompatible",
            "code": "SVC-6005",
            "message": "权重缺少模型必需的结构",
            "missingKeys": missing[:10],
            "notes": notes,
            "speechEncoder": encoder,
        }

    compressed = not any(k.startswith("enc_q.") for k in shapes)
    if compressed:
        notes.append("缺少 enc_q.*（压缩模型），推理不受影响，但不能再训练")

    return {
        "verdict": "compressed" if compressed else "ready",
        "code": None,
        "message": "结构校验通过",
        "speechEncoder": encoder,
        "clipLimitSeconds": ENCODER_LIMITS.get(str(encoder), 30.0),
        "notes": notes,
        "missingKeys": [],
    }


def read_shapes_with_torch(model_path: str) -> Tuple[Dict[str, Shape], List[str]]:
    try:
        import torch  # 延迟导入：没有 torch 时必须给出可读结论
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"当前运行时没有 torch，无法读取权重结构：{exc}") from exc

    raw = torch.load(model_path, map_location="cpu")
    return normalize_state_dict(raw)


def run_check(model_path: str, config_path: str) -> Dict[str, Any]:
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "incompatible",
            "code": "SVC-4001",
            "message": "config.json 无法解析",
            "detail": str(exc),
            "missingKeys": [],
            "notes": [],
        }

    try:
        shapes, notes = read_shapes_with_torch(model_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "unknown",
            "code": "SVC-1001",
            "message": "无法读取权重结构",
            "detail": str(exc),
            "missingKeys": [],
            "notes": [],
        }
    return check_compat(config, shapes, notes)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="svc_bridge model")
    parser.add_argument("action", choices=["check"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)

    result = run_check(args.model, args.config)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0 if result["verdict"] in ("ready", "compressed") else 2
