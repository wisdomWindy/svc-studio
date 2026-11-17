# 代码与交付评审

- 结论：**有条件通过（Conditional Approval）** —— 所有可在无 GPU 环境完成的交付项均已落地并留证；5 项验收必须有 NVIDIA GPU 才能取证，已列为放行条件。
- 评审范围：本仓库当前工作树（不含 `vendor/`、`runtime/`、`node_modules`、`artifacts/`、`src-tauri/resources/`）。
- 证据台账：`../verification/verification.md`（25 ✅ / 19 🟡 / 5 ⛔）。

## 1. 声明式放行条件（每条可复核）

| # | 声明 | 复核方式 | 当前 |
| --- | --- | --- | --- |
| R1 | 质量门全绿 | `powershell -File scripts/check.ps1` → `RESULT PASS`（10 步） | ✅ |
| R2 | 单元/集成测试通过 | `cargo test` 97 / `pytest worker/tests` 98 / `pnpm vitest run` 35 | ✅ |
| R3 | 安装包可安装、可卸载、运行时自洽 | `artifacts/0.1.0/SVC Studio_0.1.0_x64-setup.exe`（317.68MB）+ `evidence/H4/install-uninstall.json` + `evidence/H1/install-runtime-ready.json` | ✅ |
| R4 | 合规材料可解析且随包提供 | `python tools/check_compliance.py artifacts/0.1.0` → PASS（SBOM 50 组件 + AGPL 全文 + source zip） | ✅ |
| R5 | 错误码与错误用例不漂移 | `tools/check_error_codes.py`（29/29）+ `tools/check_error_cases.py`（12/12） | ✅ |
| R6 | 验收台账不虚高 | `tools/check_verification.py` → 证据可达 + 汇总与表格一致 | ✅ |
| R7 | 计划状态与磁盘一致 | `tools/check_state.py` → artifacts 31 项 + 必需交付物 23 项 | ✅ |
| R8 | 首次启动门禁生效 | `evidence/A2/consent-gate.png` + `consents` 表 4 行 + 重启不再弹 | ✅ |
| R9 | 关闭窗口不误杀任务 | `evidence/G4/after-close.png`（`alive=True/visible=False`，两次复现） | ✅ |
| R10 | 预处理链路真实执行 | `evidence/D1/d1-preprocess.json`（exit 0 / 产物各 2 / manifest passed） | ✅ |
| R11 | 训练作业链路真实执行 | `evidence/E2/train-smoke.json`（exit 0 / 102 事件 / maxStep=100）+ `bridge::host_train_e2e`（spawn→result→stop） | ✅ |
| R12 | OOM 自愈真实生效 | `pytest worker/tests/test_train_oom.py`（8→4 写回配置、只重试一次） | ✅ |
| **R13** | 2000 步训练无 NaN/Inf、峰值显存有记录 | 需 GPU 机器 | ⛔ |
| **R14** | 单条推理 44.1k/单声道/时长误差 ≤100ms | 需 GPU + 权重 | ⛔ |
| **R15** | 基线 GPU RTF ≤1.5 | 需 GPU | ⛔ |
| **R16** | 训练满载主线程 P95 <200ms（10 分钟） | 需 GPU 训练 | ⛔ |
| **R17** | 24h 浸泡无异常、内存增长 <10% | 需独占环境 24h | ⛔ |

**R1–R12 全部满足；R13–R17 需外部硬件，未满足前不得对外声称"全部验收通过"。**

## 2. 代码评审要点

| 维度 | 结论 | 说明 |
| --- | --- | --- |
| 分层 | 通过 | commands → core → infra；无反向依赖 |
| 错误契约 | 通过 | `AppError` 统一 code/cause/next_action；29 个码与文档一致 |
| 安全边界 | 通过 | capabilities 最小集、CSP 严格、`safe_join`、诊断包白名单、资产签名校验（Invalid 态已实现） |
| 资源管理 | 通过 | Job Object 杀树、GPU 单并发（Scheduler）、进度事件节流 |
| 数据一致性 | 通过 | 原子写 + 事务 + 启动对账（running→interrupted、model→missing） |
| 可测试性 | 通过 | 97/98/35；含真实子进程与真实 runtime 用例 |

## 3. 已修的真实缺陷（按发现顺序）

| # | 缺陷 | 影响 | 证据 |
| --- | --- | --- | --- |
| 1 | `svc_bridge` CLI 缺 5 个子命令分发 | 训练/推理/判定在真机静默失败（exit 2） | `test_cli_dispatch.py` |
| 2 | 冒烟到 100 步被判失败（正数退出码未归一化） | 冒烟永远报失败 | `test_train.py` exitCode 断言 |
| 3 | `aux.py` 是 Windows 保留设备名 | 打包复制中断，安装版缺模块 | 改名 `extras.py` |
| 4 | 安装版找不到 `resources/runtime` | 界面永远"运行时未就绪" | `installed-runtime-ready.png` |
| 5 | doctor JSON camelCase/snake_case 不一致 | 真实运行时自检解析失败 | `serde(alias)` + 测试 |
| 6 | GPU 失败项无修复动作 | 违反 B2 | doctor 文案 + 断言 |
| 7 | OOM 只检测不重试 | E6 名存实亡 | `test_train_oom.py` |
| 8 | 配置相对路径按 cwd 解析 | 降档改错文件 | 按项目根解析 |
| 9 | 曲线数据被协议丢弃 | 训练页无曲线 | `CURVE` 事件 + `curve.test.ts` |

## 4. 已知限制（不影响放行判断，但需告知）

1. 运行时为 **CPU 版 torch 1.13.1**（GPU 训练需另配 CUDA 运行时，安装包会增至约 2.2GB）。
2. 无 NVIDIA 显卡时 doctor 恒为 `trainable=false`，训练/预处理入口被门禁拦住——这是设计行为。
3. A/B 对照只做双波形 + 同步起播，无 seek 联动。
4. 200% 缩放、对比度、屏幕阅读器走查未做。
5. 干净 VM 全流程（W9-5）未做，仅本机安装/卸载验证。

## 5. 放行建议

**建议以"技术预览版"交付**：可安装、可打开、可导入数据、可跑 QC/预处理/训练编排（CPU 环境到门禁为止）、可看曲线、可管理模型、可导出合规材料。

**不建议以"正式版"交付**：R13–R17 未取证，真实音色质量未验证。补齐条件：一台 ≥6GB 显存的 NVIDIA 机器 + 至少一个可用的 so-vits-svc 模型权重。
