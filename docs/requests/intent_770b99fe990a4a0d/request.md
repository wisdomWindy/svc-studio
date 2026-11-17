# 请求目标契约：SVC Studio

- request_id：`intent_770b99fe990a4a0d`
- 交付单元：单个（非拆分 PRD，`module_flow = null`）
- 目标（可观察）：在一台**未安装 Python** 的 Windows 11 + NVIDIA 6GB 机器上，安装后完成
  「导入数据集 → 一键预处理 → 100 步冒烟 → 长训 → 试听/导出」全链路，每步可中断、可恢复、失败可读；
  另支持把**外部模型**（非本软件训练）拖进上传区后直接推理，输出落独立批次目录。
- 非目标：TTS、云端、macOS、批量模型导入、模型市场、真人声音授权代办。
- 完成判据：工作台验收清单 `A1…I2` 全部通过，或未通过项有用户签署豁免；`verification.md` 与 `review.md` 结论为通过。
- 权威上游工件：
  - 产品需求：[PRD.md](PRD.md)（v0.6，含 §5.9.1 上传区）
  - 需求-验收矩阵：[requirement-splitting/traceability.md](requirement-splitting/traceability.md)
  - 事实基线：[intake.md](intake.md) §4（C-01…C-18）与 [prd/appendix-a-parameters.md](prd/appendix-a-parameters.md)
  - 合规：[compliance.md](compliance.md)
- 说明：本请求不使用 `artifacts/prd-snapshot.md`，`PRD.md` 即产品快照；不使用 `requirements/requirement-map.md`，`requirement-splitting/traceability.md` 即需求映射。
