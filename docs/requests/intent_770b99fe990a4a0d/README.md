# 请求工作区：SVC Studio（so-vits-svc 4.1-Stable 桌面端）

- request_id：`intent_770b99fe990a4a0d`
- 当前阶段：`page-design`（待用户评审 PRD 后启动）
- 状态：PRD 已交付，S0/S1 门禁通过；未开始编码

## 文档导航

| 文档 | 作用 | 读者 |
| --- | --- | --- |
| [PRD.md](PRD.md) | **主交付物**：定位、用户、旅程、功能规则、页面、状态机、NFR、排期、风险 | 全员 |
| [prd/appendix-a-parameters.md](prd/appendix-a-parameters.md) | 上游入口点与参数契约、互斥矩阵、硬断言、与上游默认值的差异 | 工程 / 测试 |
| [prd/appendix-b-error-codes.md](prd/appendix-b-error-codes.md) | 错误码表（含 G2 必测 12 类）与文案规则 | 工程 / 测试 / 设计 |
| [prd/appendix-c-data-model.md](prd/appendix-c-data-model.md) | SQLite 表结构、原子写、报告 JSON、导出元数据 | 工程 |
| [intake.md](intake.md) | 目标、用户、硬件、18 条源码事实基线、10 条旅程 | 全员 |
| [requirement-analysis.md](requirement-analysis.md) | 来源标注、责任划分、歧义裁决、5 个待确认决策 | 产品 / 架构 |
| [compliance.md](compliance.md) | AGPL 义务、使用规约落地、隐私与溯源、阻断条件 | 法务 / 发布 |
| [requirement-splitting/requirements.md](requirement-splitting/requirements.md) | 44 条需求（Given/When/Then，P0=12） | 工程 / 测试 |
| [requirement-splitting/traceability.md](requirement-splitting/traceability.md) | 需求 ↔ 验收双向矩阵（脚本生成） | 测试 / 评审 |

## 自检命令

```powershell
python docs/requests/intent_770b99fe990a4a0d/tools/check_traceability.py
python docs/requests/intent_770b99fe990a4a0d/tools/check_artifacts.py
```

预期：两条均输出 `RESULT PASS`、退出码 0。

## 门禁状态

| 门禁 | 结论 | 证据 |
| --- | --- | --- |
| G0 intake | pass | 范围含 TTS 排除、合规含源码提供方式、10 条旅程有判据 |
| G1 requirement-splitting | pass | `requirements=44 p0=12 acceptance=49 covered=49`，孤儿 0、重复 0 |
| G2 spec/spike | 未开始 | 需先完成 page-design 与 ADR-001..003 |
