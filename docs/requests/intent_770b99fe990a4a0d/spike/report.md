# Spike 报告（架构假设的技术验证）

> 目的：在写 spec/plan 前后，用最小可运行代码验证"这条路走得通"，而不是等集成阶段才发现假设不成立。
> 结论：**四条假设已验证；一条（真实 torch 推理）未能验证，原因与处置在最后一节写明。**

## S-1 子进程宿主 + 进程树终止（ADR-002）

**假设**：Rust 侧能用 Job Object 管住 Python worker 及其孙进程，取消时整棵树退出。

**做法**：`bridge/host` + `infra/job_object`，fixture 里让 worker 再起一个孙进程；`stop()` 走 `TerminateJobObject`。

**结果**：
- `host_train_e2e::stop_kills_runner_within_grace`：真实 spawn 后 5s 内 `killed=true`，总耗时 <10s；
- 孙进程一并退出（`kill_tree` 用例）；
- 日志流有 16ms 合流背压，不会淹没前端。

**结论**：假设成立。已在 `core/runtime` 与 `commands/train` 落地。

## S-2 冒烟到上限能自己停（训练可控性）

**假设**：给 train.py 传 `--smoke-steps` 并由宿主在到达上限时终止，就能在 10 分钟内验证"能落盘、能续训"。

**结果**：
- 真实 worker CLI：exit 0、102 条事件、`maxStep=100`；
- 发现并修掉一个真 bug：Windows 下主动终止返回**正数**退出码，原逻辑只归一化负数，导致冒烟永远失败。

**结论**：假设成立，但"退出码归一化"必须显式处理。

## S-3 关闭窗口不中断任务（桌面交互）

**假设**：拦截 `CloseRequested` → `prevent_close + hide`，配合托盘菜单"退出"，可以在关闭窗口时保住后台任务。

**结果**（两次复现）：
- `WM_CLOSE` 后 `alive=True`、`visible=False`；
- 桌面截图确认窗口消失、进程仍在。

**结论**：假设成立。托盘图标在系统托盘溢出区未能截图确认，不作为证据。

## S-4 主线程延迟可测（G1 前置）

**假设**：用 `setTimeout(计划间隔)` 的超出量能测出主线程阻塞。

**结果**：5 秒、227 样本、p95 3.2ms、超 200ms 0 次（IPC 施压下）。

**结论**：测量方法可用；但**这不是 G1 验收**，因为 G1 要求"训练满载"，本机无 GPU。

## S-5 真实 torch 推理 / 100 步训练（未验证）

**假设**：内置 Python 3.8.9 + torch 1.13.1+cu117 能跑通 `inference_main` 等价调用与 `train.py`。

**未能验证的原因**：目标运行时约 2.2GB，本机实测下载速率约 2.2KB/s（8MB 安装包 20 分钟只到 2.61MB），按此速率需约 11 天；且本机无 NVIDIA GPU、无模型权重。

**风险与处置**：
- 这条假设仍是 **R1/R2 的未闭环部分**；
- 已用"真实 worker 进程 + 假 upstream 脚本"验证了**编排层**（D1/E2/E5），把风险收敛到"torch 环境本身"；
- 一旦拿到 runtime 压缩包或高速网络，按 `scripts/build-runtime.ps1 → stage-runtime.ps1 → release.ps1` 顺序即可补齐 H1/H2/H3/H4，无需改代码。

## 对 spec 的影响

| 假设 | 结果 | spec 变更 |
| --- | --- | --- |
| 进程树终止 | 成立 | 无（ADR-002 已定） |
| 冒烟可控 | 成立（需归一化退出码） | 增补"主动终止视为成功"的约定 |
| 关闭到托盘 | 成立 | 无 |
| 主线程可测 | 成立 | G1 验收需真实训练负载 |
| torch 环境 | **未验证** | 保留 R1/R2，验收前必须补 |
