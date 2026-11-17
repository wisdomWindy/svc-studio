# 计划自查（WP0–WP9 vs 实际代码/证据）

> 目的：找出"任务板写着 done、实际没做"或反过来的项。
> 方法：对每个可疑项跑任务板里写的验收命令，记录真实输出。
> 结论：发现 **6 处状态失实**，已全部纠正；没有发现新的功能缺口。

最后更新：2026-11-17。

## 一、失实项与纠正

| 项 | 任务板原状态 | 实际 | 证据 | 纠正 |
| --- | --- | --- | --- | --- |
| W3-3 doctor 九项 | `todo` | **已完成**（9 项、trainable 判定） | `runtime\python.exe -m svc_bridge doctor --json` → `items 9 trainable False` | 改为 done |
| W6-6 OOM 一次降档自愈 | `done` | **原先只有检测、没有重试**；本轮才实现 | `pytest test_train_oom.py` → 2 passed；断言配置里 batch_size 8→4 | 实现后维持 done |
| W9-1 内置 runtime | 脚本就绪 | **已真实构建**（Py3.8.9 + torch1.13.1+cpu） | `runtime\python.exe` → `rt 3.8.9 1.13.1+cpu` | 改为 done |
| W9-2 resources + DLL 清单 | 脚本就绪 | **已 staged 并校验**（11783 文件 / 0.88GB） | `verify-dlls.ps1` → 运行时类型 cpu，通过 | 改为 done |
| W9-3 NSIS + WebView2 离线 | 脚本就绪 | **已产出 317.68MB 安装包并安装成功** | `artifacts/0.1.0/SVC Studio_0.1.0_x64-setup.exe` | 改为 done |
| W9-4 SBOM / licenses / source | 脚本就绪 | **已生成并通过合规自检** | `release.ps1 -SkipBuild` → PASS；SBOM 50 组件 | 改为 done |
| W8-3 波形 | 部分 | 波形 + 同步起播/暂停已实现（无 seek 联动） | `vitest src/components/AudioPair.test.tsx` | 保持部分（seek 未做） |
| W8-4 8 页接命令 | 部分 | 8 页全部接真实命令 | 各页面代码 + 命令契约测试 | 改为 done（A/B 已接批次） |

## 二、按命令抽查（全部实跑）

| 命令 | 结果 |
| --- | --- |
| `cargo test` | 97 passed |
| `pytest worker/tests` | 98 passed |
| `pnpm vitest run` | 31 passed |
| `python -m svc_bridge doctor --json` | 9 项，trainable=False（无 GPU/权重） |
| `runtime\python.exe -m svc_bridge --version` | 0.1.0 |
| `runtime\python.exe -c "import torch"` | 1.13.1+cpu，Python 3.8.9 |
| `scripts/verify-dlls.ps1` | 真实运行时通过（CPU 类型） |
| `scripts/release.ps1 -SkipBuild` | 合规自检 PASS，产物 317.68MB + SBOM + source |
| `scripts/error-cases.ps1` | 9 PASS / 3 SKIP / 0 FAIL |
| `tools/check_error_codes.py` | 29 / 29，零漂移 |
| `tools/check_verification.py` | 证据可达，汇总一致 |
| `tools/check_state.py` | artifacts 31 项、必需交付物 23 项 |

## 三、仍未完成的（诚实清单）

| 项 | 原因 | 补齐条件 |
| --- | --- | --- |
| W9-5 干净 VM 全流程 | 无可用干净 VM | 提供 VM 后跑 `verify-clean-vm.ps1` |
| W8-6 无障碍走查 | 未做 | 键盘/200% 缩放人工清单 |
| W8-3 seek 联动 | 未做 | A/B 波形拖动同步 |
| E3/F1/F3/G1/G5 | 无 NVIDIA GPU | 上 GPU 机器 |
| 真实 100 步训练 / 推理 | 无模型权重 | 提供权重或授权下载 |

## 四、方法学结论

这一轮印证了一件事：**"任务板写 done" 不等于 "代码里有"**。本轮自查抓到的 OOM 自愈就是典型——功能名在板上、错误码在文档里、唯独重试逻辑不存在。建议把本文件的"抽查命令"表纳入每次发版前的手工检查项，而不是只看任务板状态。
