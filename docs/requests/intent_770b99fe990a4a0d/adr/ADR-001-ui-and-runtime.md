# ADR-001：客户端技术栈与运行时边界（Tauri 2 + Rust + Python worker）

- 状态：**已接受（2026-11-16 修订，取代原 PySide6 方案）**
- 决策者：架构 / 产品
- 关联：spec §3、§6、§13、§16；architecture-design §2/§3/§12；PRD §2、§12

## 1. 背景

本产品要在一台**没有 Python 的 Windows 机器**上完成：环境自检 → 数据清洗 → 特征预处理 → PyTorch 训练（长任务、GPU 独占）→ 推理。UI 只需要做三件事：展示状态、收集参数、播放音频。

原方案是 PySide6（Qt 6）单进程 + Python worker，理由是"与 torch 同语言、少一层"。用户现在指定 **Tauri 技术栈**，因此必须重新论证：Tauri 的 Rust 壳 + Web 前端，如何承载一个必须依赖 CPython 3.8.9 + torch + fairseq 的计算内核。

关键事实（约束，不可协商）：

1. 计算内核**只能是 Python**：fairseq 0.12.2、torch、上游 `train.py`/`inference` 全是 Python 生态，Rust/JS 重写不现实（成本 > 12 人月，且要重训基线）。
2. 训练是**长任务、独占 GPU、随时可能 OOM 或被强杀**，必须与 UI 进程隔离。
3. 全离线、零遥测；安装包要自带运行时（用户无 Python）。
4. 上游 `train.py` 以相对路径工作，必须指定 cwd。

## 2. 决策

**采用 Tauri 2 作为客户端外壳，三层分工：**

| 层 | 技术 | 职责 | 明确不做 |
| --- | --- | --- | --- |
| 呈现层 | React 19 + TypeScript + Vite + Tailwind CSS，运行在 WebView2 | 渲染、输入、播放、图表 | 不碰文件系统/进程/DB（无 `fs`/`shell` 插件权限） |
| 核心层 | Rust（tokio + rusqlite + serde） | 任务状态机、队列、SQLite、原子写、sha256、进程托管（Job Object）、IPC | 不加载 torch、不做推理 |
| 计算层 | 内置 CPython 3.8.9 + 上游源码 + `svc_bridge` | 预处理/训练/推理/导入校验 | 不写 UI、不写 DB |

**层间契约**：前端 ↔ Rust 用 Tauri IPC（`command` 请求/应答 + `Channel` 单向流，事件 100ms 合并）；Rust ↔ Python 用 **stdin/stdout JSON Lines**（spec §6.2），每 job 一个 worker 进程。

**Python 运行时**：不再用 PyInstaller 打包成单 exe，而是**内置一整套 CPython 3.8.9（python-build-standalone）+ 已装好的 site-packages + 上游源码**，作为 Tauri `resources` 随包分发；Rust 直接 `spawn resources/runtime/python.exe resources/worker/worker.py`。

## 3. 备选方案与否决理由

| 方案 | 结论 | 理由 |
| --- | --- | --- |
| **Tauri 2 + Rust 核心 + Python sidecar** | **采用** | 前端生态成熟（波形/图表/上传区实现快）；Rust 负责最需要可靠性的部分（进程树、原子写、DB）；安装包比 Electron 小一个量级；Python 只做它擅长的事 |
| PySide6（原方案） | 否决（用户指定更换） | 同语言省一层，但 UI 迭代慢、复杂控件（A/B 同步波形、虚拟列表）实现成本高；Qt 部署与高 DPI 坑多 |
| Electron + Node 编排 + Python sidecar | 否决 | 同为 Web 前端，但多打包一个 Chromium（+120MB）、内存更高、进程模型更重；Rust 侧能力（Job Object、原子写、校验和）在 Node 里要么调三方包要么写 native addon |
| Wails（Go 壳） | 否决 | 生态与 Tauri 接近但前端能力弱一档（无官方 Channel 流、插件少）；Go 侧做 Windows Job Object 也要 cgo |
| .NET 8 + WPF/WinUI 3 | 否决 | 原生性能好，但前端迭代慢、跨团队协作差；WPF 的音频/波形控件几乎要自研 |
| 纯 Rust（egui / iced） | 否决 | 无 Web 生态，上传区/拖拽/波形/图表全部自研，工期不可控 |
| Rust 重写推理内核 | 否决 | 需重实现 VITS/NSF-HiFiGAN/ContentVec，且无法复用上游权重与既有基线 |

## 4. 为什么 Rust 接管编排层（而不是继续用 Python 编排）

原 PySide6 方案里编排层是 Python（QThread + JobManager）。改栈后有两个选择：

- **A：Rust 只做壳，编排仍放 Python 常驻进程**（Tauri ↔ Python 双常驻）
- **B：Rust 做编排，Python 只按 job 起停**（本决策）

选 B，理由：

1. **崩溃面**：编排进程若与 torch 同进程，torch/OOM/驱动崩溃会连带任务状态机一起死；B 里 Rust 永不被 torch 污染。
2. **启动**：A 的常驻 Python 一启动就得 import torch（3–8s），冷启动直接超标；B 只在任务开始才起 worker。
3. **DB 事务**：Rust 侧同步 SQLite + 显式事务，比 Python 侧跨线程队列更简单可靠。
4. **打包**：B 不需要把编排逻辑做成 Python 包入口，资源边界更清晰。

代价：Rust 需要写一遍 JobManager/ProcessHost/AssetVerifier（约 3–4k 行），且团队要会 Rust。已记录为风险 R-ARCH-01。

## 5. 后果

**正面**
- 去掉 PyInstaller 这个一级风险（原 spec A2）：直接带 CPython + site-packages，torch/CUDA DLL 不需要"猜 hiddenimports"。
- 前端能低成本做到：拖拽上传、A/B 同步波形、虚拟化万行表格、深浅色主题。
- 冷启动目标从 ≤8s 收紧到 ≤3s。
- 渲染进程零系统权限，WebView 被攻破也拿不到文件系统（对比 Qt 单进程直接持有权限）。

**负面 / 代价**
- 新增 WebView2 依赖：Win11 自带；Win10 22H2 干净机可能没有，需安装器内置 Evergreen 离线包（约 130MB，A8）。
- 两套语言（Rust + TS + Python）三个工具链：CI 要分别跑 cargo test / vitest / pytest。
- 安装包构成变化：从 `SVCStudio.exe + _internal\` 变成 `SVCStudio.exe + resources\runtime\ + resources\worker\ + resources\assets\`，资源路径解析要用 Tauri `resource_dir()`，不能写死相对路径。
- 团队需要 Rust 能力；招聘/交接成本上升。

## 6. 落地约束（写进 plan 的硬要求）

1. 前端**禁用** Tauri `fs`、`shell`、`http` 插件；所有文件/网络操作走自定义 command（spec §13）。
2. Rust IPC handler 里禁止同步长操作：sha256、目录遍历、DB 批写一律 `spawn_blocking` 或下沉 worker。
3. `resources/runtime` 必须**只读**使用；worker 的 `sys.path` 注入由 Rust 通过环境变量传递，不改安装目录。
4. 进程托管必须用 Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），并保留 `taskkill /T /F` 兜底（A9）。
5. 前端打包产物必须自包含（Vite 构建到 `dist/`，Tauri `frontendDist` 指向它），release 关闭 devtools。

## 7. 验证方式

- 冷启动计时 5 次取中位数（H5）；
- 强杀 worker 观察前端存活（G3）；
- 断网安装 + 首启（H2）；
- `cargo tree` 与 `pnpm list` 产物进 SBOM（A3）。

## 2026-11-17 修订：运行时合并

原方案保留 Python 3.8.9（上游官方基线）与 GPU 运行时两套。实测四条上游链路（preprocess_flist_config / preprocess_hubert_f0 / train / inference_main）在 Python 3.13 + torch 2.8.0+cu128 + fairseq-fixed 下全部通过，故合并为**唯一 GPU 运行时**，删除 CPU 运行时与模式切换。代价：安装包约 3.4GB（超过早期 2.2GB 预算，属用户明确选择）。
