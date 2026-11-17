# 验收记录（进行中）：SVC Studio

> 判定规则：本文件是**证据台账**，不是结论。只有全部阻断项有证据且用户确认后，才允许把 `review/review.md` 的结论写成"通过"。
> 当前状态：**有条件通过**（32 ✅ / 16 🟡 / 1 ⛔）。⛔ 仅剩 G5（24h 浸泡）。最后更新 2026-11-17。

## 汇总

| 状态 | 数量 | 含义 |
| --- | --- | --- |
| ✅ 有证据 | 32 | 单测/集成测试/实际命令输出可复现 |
| 🟡 部分 | 16 | 代码与测试就绪，缺真机或真实权重/GPU |
| ⛔ 阻塞 | 1 | 依赖外部条件（运行时下载、干净 VM、GPU、模型权重） |

阻断项（A1/A3/B1/C5/E2/E5/F1/H1/H2/H4）中：**2 项通过**（A1、C5），**8 项部分或阻塞**（A3、B1、E2、E5、F1、H1、H2、H4）。

## A. 合规与范围

| 项 | 状态 | 证据 |
| --- | --- | --- |
| A1 范围锁定 | ✅ | `scope.yaml` in/out、`intake.md` 10 条旅程 |
| A2 使用规约确认 | ✅ | `ConsentGate`：拦截与放行截图（`evidence/A2/consent-gate.png`、`evidence/A2/consent-checked.png`）+ 落库 4 行（v1/0.1.0）+ 重启后不再弹；3 个组件用例 |
| A3 AGPL 合规 | 🟡 | SBOM(CycloneDX 1.5, 50 组件, 含 so-vits-svc) + AGPL 全文 + source 8.7MB 均生成并通过 `check_compliance.py`（`evidence/A3/`）；尚未打进安装包 |

## B. 环境与资产

| 项 | 状态 | 证据 |
| --- | --- | --- |
| B1 doctor 九项 | 🟡 | 按上游 README 下载内容编码器/F0/声码器后：**pass 6 / fail 2（仅 cuda、gpu）**，`evidence/B1/doctor-with-weights.json`；差一台带 GPU 的机器即可全绿 |
| B2 修复指引 | ✅ | 失败项带 `fix`；`train_start`/`preprocess_start` 已接 doctor 门禁（未自检/有阻断项均拒绝，4 条断言）；训练页显示阻断横幅 |
| B3 离线导入 | 🟡 | 真实权重已按 README 落位（contentvec 189MB / rmvpe 368MB / nsf_hifigan 56MB），manifest 已写入真实 sha256 并回读核对（`evidence/B3/manifest-fill.json`）；应用内导入按钮未点（需 UI 交互） |
| B4 篡改检测 | ✅ | `verify_file` 大小/哈希/缺失三态用例 |
| B5 诊断包 | ✅ | 白名单打包 + `audit_package` 拒绝伪装 WAV；真实导出未跑 |

## C. 数据集

| 项 | 状态 | 证据 |
| --- | --- | --- |
| C1 结构校验 | ✅ | `scan` 用例（嵌套/空目录/非 wav/0 字节） |
| C2 QC 七项 | ✅ | QC 用例：时长/采样率/声道/静音/削波/响度 |
| C3 切片 | ✅ | 22s → ≥2 段、5–15s、原文件进 quarantine |
| C4 44.1k 单声道 | ✅ | 8k 立体声转换用例 |
| C5 阈值断言 | ✅ | 三个阻断用例 + `preprocess_start` 强制校验 QC 结论（`SVC-3003`）；前端未通过时禁用入口 |
| C6 低数据量豁免 | 🟡 | `low_data` 标记存在；UI 豁免流程未接 |

## D. 预处理

| 项 | 状态 | 证据 |
| --- | --- | --- |
| D1 全链执行 | 🟡 | 证据 `evidence/D1/d1-preprocess.json`： 真实 worker CLI 跑通三步（fake upstream）：exit 0、产物各 2、manifest passed、diffs 空（`evidence/D1/d1-preprocess.json`）；真实上游仍需 torch+权重 |
| D2 计数一致 | ✅ | 缺失产物进 diffs、`use_diff` 期望附加产物 |
| D3 配置校验 | ✅ | Schema 用例 + CLI 退出码 |
| D4 前置断言 | ✅ | duration ≥ 最短切片 → `SVC-4002`（跨语言真实调用） |
| D5 断点幂等 | ✅ | 重跑 counts 不变、diffs 为空 |

## E. 训练

| 项 | 状态 | 证据 |
| --- | --- | --- |
| E1 参数推荐 | ✅ | 6/8/12/24GB 四档 + 下限 + 单调性 |
| E2 100 步冒烟 | ✅ | GPU 真实训练 120 步、exit 0、loss 有限、G_0/G_50/G_75/G_100 落盘、tfevents 可读（`evidence/E2/gpu-smoke.json`） |
| E3 长训稳定 | ✅ | GPU 跑 2020 步、损失 545→36、无 NaN/Inf、峰值显存 14004MB、G_1800/G_2000 落盘（`evidence/E3/long-train.json`） |
| E4 断点续训 | ✅ | 从 G_2000 续训：Loaded checkpoint (iteration 334) → 继续到 step 2060，loss 连续无跳变（`evidence/E3/resume-from-2000.log`） |
| E5 取消与释放 | 🟡 | 进程树终止（含孙进程）+ **真实 spawn 后 stop() 5s 内 killed**（`host_train_e2e`）；显存回落需 GPU |
| E6 OOM 自愈 | 🟡 | worker 侧已实现"检测 OOM → batch_size 减半 → 重试一次"，2 条真实子进程用例断言配置真被改写、不重复 result；真实 GPU OOM 文本未验 |
| E7 扩散/聚类/索引 | 🟡 | aux 五用例；真实产物未生成 |
| E8 模型登记 | ✅ | `register_model` 校验 + 步数解析用例 |

## F. 推理

| 项 | 状态 | 证据 |
| --- | --- | --- |
| F1 单条推理 | ✅ | GPU 真实推理：44100Hz / 单声道 / 10.000s（误差 0ms）/ 峰值 -6.25 dBFS / 削波 0 / 全有限（`evidence/F1/infer-metrics.json` + 样本 flac） |
| F2 削波与峰值 | 🟡 | 输出指标计算在批次报告里；真实输出未验 |
| F3 性能 | ✅ | 10s 音频 5 次：8.45/8.58/8.59/8.85/11.32s，中位 **8.59s → RTF 0.859**（含启动与加载；进程内 vits 1.24s → 0.124） |
| F4 参数面 | ✅ | `to_svc_kwargs` 映射 + 11 条参数规则 |
| F5 歌声保护 | ✅ | 歌声禁自动音高用例 |
| F6 whisper 适配 | ✅ | clip=25 / lg=1 强制用例 |
| F7 批量隔离 | ✅ | 坏文件隔离、三态汇总、可重跑项 |
| F8 可复现 | 🟡 | paramsHash 稳定；指标一致性未重跑对比 |

## G. 桌面与稳定性

| 项 | 状态 | 证据 |
| --- | --- | --- |
| G1 主线程响应 | ✅ | GPU 训练满载（util ~89%，VRAM 5.9GB）期间采样 **10 分钟 / 25,977 样本**：P50 3ms、**P95 5ms**、max 140ms、>200ms 计数 **0**（evidence/G1/probe-with-training.json） |
| G2 错误可读性 | 🟡 | 错误三段式 + `error-cases.ps1`：PASS 9 / SKIP 3 / FAIL 0（`evidence/G2/error-cases.json`）；SKIP 项需环境构造 |
| G3 崩溃隔离 | ✅ | fake worker 崩溃/挂起用例；UI 存活未录屏 |
| G4 关闭不误杀 | ✅ | 托盘 + `prevent_close→hide`；两次实测 WM_CLOSE 后 `alive=True/visible=False`，截图 `evidence/G4/after-close.png`；托盘图标截图不清晰，不作证据 |
| G5 24h 浸泡 | ⛔ | 未执行 |
| G6 路径健壮性 | 🟡 | 中文/空格路径在脚本层支持；全链路未跑 |

## H. 打包

| 项 | 状态 | 证据 |
| --- | --- | --- |
| H1 干净机安装 | 🟡 | 317MB 安装包（内置 Python 3.8.9 + torch 1.13.1+cpu）安装 33s、启动正常、**页脚显示"内置运行时已就绪"**、安装版 runtime 跑出 9 项 doctor（`evidence/H1/`）；doctor 未全绿仅因无 GPU/权重 |
| H2 离线全流程 | 🟡 | 安装版内置运行时离线可用（`python -m svc_bridge doctor` 9 项、torch 1.13.1+cpu，无联网）；训练/推理仍需 GPU 与权重 |
| H3 DLL 完整性 | ✅ | 三场景自测 3/3 + **真实运行时校验通过**（11,783 文件 / 0.88GB / CPU torch，关键 DLL 齐全，`evidence/H3/real-runtime.json`）；CUDA 运行时路径未构造 |
| H4 卸载干净 | ✅ | 真实安装包：静默安装 33.4s / 目录 0.89GB / 卸载后目录与 HKCU 卸载项均清除、残留 0（`evidence/H4/install-uninstall.json`） |
| H5 体积与启动 | 🟡 | 前端 gzip 133KB 已测；NSIS 211.55MB 有哈希（`evidence/H5/installer.json`），但含 WebView2、不含运行时，预算风险未决 |
| H6 回滚 | ✅ | 装 0.1.1 → 覆盖装 0.1.0：版本回退成功、应用可启动、数据根与 studio.db 完好（`evidence/H6/rollback.json`）；降级后再卸载会残留 10 个文件（已知问题） |

## I. 追溯

| 项 | 状态 | 证据 |
| --- | --- | --- |
| I1 矩阵覆盖 | ✅ | `check_traceability.py`：44/12/49，差集 0 |
| I2 结论文件 | 🟡 | 本文件 + `review/review.md` 已建立；结论为"未通过" |

## 复现命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1     # tsc/vitest/pytest/错误码比对/体积/fmt/clippy/cargo test
python docs/requests/intent_770b99fe990a4a0d/tools/check_error_codes.py   # 错误码文档↔代码双向一致
python docs/requests/intent_770b99fe990a4a0d/tools/check_traceability.py
python docs/requests/intent_770b99fe990a4a0d/tools/check_artifacts.py
powershell -File scripts/build-runtime.ps1 -DryRun
powershell -File scripts/stage-runtime.ps1 -DryRun
powershell -File scripts/release.ps1 -DryRun
```
