"""推理参数规则（附录 A.5/A.6）：正常化 + 硬校验。

所有互斥关系只在这里实现一次，UI 与命令层都调用它，避免"界面禁用了但命令还传"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

WHISPER_ENCODERS = {"whisper-ppg", "whisper-ppg-large"}
F0_PREDICTORS = {"crepe", "pm", "dio", "harvest", "rmvpe", "fcpe", "none"}

DEFAULTS: Dict[str, Any] = {
    "trans": 0,
    "f0Predictor": None,
    "autoPredictF0": False,
    "clusterModelPath": "",
    "clusterInferRatio": 0.0,
    "featureRetrieval": False,
    "shallowDiffusion": False,
    "kStep": 100,
    "enhance": False,
    "loudnessEnvelopeAdjustment": 1.0,
    "useSpkMix": False,
    "clip": 0.0,
    "linearGradient": 0.0,
    "sliceDb": -40,
    "noiseScale": 0.4,
    "padSeconds": 0.5,
    "wavFormat": "flac",
    "secondEncoding": False,
    "onlyDiffusion": False,
}


def _issue(code: str, field: str, message: str, detail: str = "") -> Dict[str, str]:
    return {"code": code, "field": field, "message": message, "detail": detail}


def normalize(
    params: Dict[str, Any],
    *,
    encoder: Optional[str],
    material_type: str,
    has_cluster: bool,
    has_index: bool,
    has_diffusion: bool,
) -> Dict[str, Any]:
    """返回 {effective, warnings, blocking}；不修改入参。"""
    effective = dict(DEFAULTS)
    effective.update({k: v for k, v in params.items() if v is not None})
    warnings: List[Dict[str, str]] = []
    blocking: List[Dict[str, str]] = []

    if effective.get("f0Predictor") not in F0_PREDICTORS:
        effective["f0Predictor"] = None  # 交给调用方用训练时的值填充

    # none = 不跑 F0 预测器：音高条件置零，只保留音色（歌声会失去旋律）
    if effective.get("f0Predictor") == "none":
        warnings.append(
            _issue(
                "SVC-6011",
                "f0Predictor",
                "不使用 F0 预测器：音高条件置零，只保留音色",
                "适合念白/无旋律素材；歌声会明显跑调",
            )
        )

    # whisper 系列：切片与交叉淡入必须固定，否则上游无法正常推理
    if encoder in WHISPER_ENCODERS:
        if float(effective.get("clip") or 0) != 25.0:
            warnings.append(
                _issue(
                    "SVC-6004",
                    "clip",
                    "whisper 编码器强制 clip=25",
                    f"原值 {effective.get('clip')}",
                )
            )
        if float(effective.get("linearGradient") or 0) != 1.0:
            warnings.append(
                _issue(
                    "SVC-6004",
                    "linearGradient",
                    "whisper 编码器强制 linear_gradient=1",
                    f"原值 {effective.get('linearGradient')}",
                )
            )
        effective["clip"] = 25.0
        effective["linearGradient"] = 1.0

    # 歌声禁止自动音高
    if material_type == "singing" and effective.get("autoPredictF0"):
        warnings.append(
            _issue(
                "SVC-6001",
                "autoPredictF0",
                "歌声转换禁止开启自动音高预测（会严重跑调）",
            )
        )
        effective["autoPredictF0"] = False

    # 浅扩散与增强器互斥
    if effective.get("shallowDiffusion") and effective.get("enhance"):
        warnings.append(
            _issue(
                "SVC-6001",
                "enhance",
                "开启浅扩散时增强器会被禁用",
            )
        )
        effective["enhance"] = False

    # 特征检索与聚类互斥
    if effective.get("featureRetrieval"):
        if effective.get("clusterModelPath") and not has_index:
            blocking.append(
                _issue(
                    "SVC-6003",
                    "clusterModelPath",
                    "开启了特征检索，但没有可用的索引文件",
                )
            )
        effective["clusterModelPath"] = effective.get("clusterModelPath") or ""
    elif effective.get("clusterModelPath") and not has_cluster:
        blocking.append(
            _issue(
                "SVC-6003",
                "clusterModelPath",
                "指定了聚类模型，但该模型没有聚类产物",
            )
        )

    ratio = float(effective.get("clusterInferRatio") or 0.0)
    if ratio < 0 or ratio > 1:
        blocking.append(
            _issue("SVC-4001", "clusterInferRatio", "混合比例必须在 0–1 之间", str(ratio))
        )
    if ratio > 0 and not (has_cluster or has_index):
        blocking.append(
            _issue(
                "SVC-6003",
                "clusterInferRatio",
                "没有聚类/索引却设置了混合比例",
                str(ratio),
            )
        )

    # 比例 0 时清空路径，避免上游去加载不存在的模型
    if ratio == 0:
        effective["clusterModelPath"] = ""

    if effective.get("onlyDiffusion") and not has_diffusion:
        blocking.append(
            _issue(
                "SVC-6001",
                "onlyDiffusion",
                "纯扩散模式需要已训练的扩散模型",
            )
        )

    trans = int(effective.get("trans") or 0)
    if trans < -24 or trans > 24:
        blocking.append(_issue("SVC-4001", "trans", "变调范围 -24..+24 半音", str(trans)))

    if not (-60 <= int(effective.get("sliceDb") or -40) <= -20):
        blocking.append(_issue("SVC-4001", "sliceDb", "slice_db 范围 -60..-20"))

    ns = float(effective.get("noiseScale") or 0.4)
    if ns < 0 or ns > 1:
        blocking.append(_issue("SVC-4001", "noiseScale", "noise_scale 范围 0..1"))

    lea = float(effective.get("loudnessEnvelopeAdjustment") if effective.get("loudnessEnvelopeAdjustment") is not None else 1.0)
    if lea < 0 or lea > 1:
        blocking.append(_issue("SVC-4001", "loudnessEnvelopeAdjustment", "响度包络比例 0..1"))

    if effective.get("wavFormat") not in ("flac", "wav"):
        blocking.append(_issue("SVC-4001", "wavFormat", "输出格式只支持 flac / wav"))

    return {"effective": effective, "warnings": warnings, "blocking": blocking}
