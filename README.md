# SVC Studio

so-vits-svc 4.1-Stable 的 Windows 桌面产线：导入数据 → 一键预处理 → 训练监控 → 试听导出。
**SVC（音色转换），不是 TTS。**

| 层 | 技术 |
| --- | --- |
| 呈现 | React 19 + TypeScript + Vite + Tailwind（WebView2） |
| 核心 | Rust + Tauri 2（任务状态机、SQLite、进程树、原子写、sha256） |
| 执行 | 内置 Python 3.13 + torch 2.8.0+cu128 + fairseq-fixed（`worker/svc_bridge`） |

## 快速开始

```powershell
git clone https://github.com/wisdomWindy/svc-studio.git
cd svc-studio
pnpm install
pnpm run setup        # 下载内置运行时（约 7.6GB）+ 预训练权重（约 600MB），并跑环境自检
pnpm run tauri dev    # 打开桌面窗口
```

`pnpm run setup` 做的事：

1. 数据目录默认 `%USERPROFILE%\SVCStudio`（可用 `SVCSTUDIO_DATA_ROOT` 覆盖）；
2. 构建内置运行时到 `runtime/`：Python 3.13 embeddable + torch 2.8.0+cu128 + 训练依赖 + fairseq-fixed + demucs（下载约 4GB，解压后约 7.7GB，需要磁盘 ≥12GB）；
3. 下载权重到 `<数据目录>/pretrain`：ContentVec、RMVPE、NSF-HiFiGAN（来自 hf-mirror）；
4. 用内置运行时跑 `doctor`，告诉你还缺什么；5. 真发一次 CUDA kernel，明确告诉你 **GPU 是否可用**（能训练 / 只能 CPU 推理）。

已有环境时可跳过：`pnpm run setup -- -SkipRuntime` / `-SkipAssets`。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `pnpm run dev` | 浏览器预览（无后端） |
| `pnpm run tauri dev` | 桌面窗口（开发） |
| `pnpm run check` | 质量门：tsc / vitest / pytest / 错误码比对 / 体积 / fmt / clippy / cargo test |
| `pnpm run build:release` | 瘦安装包（不含运行时，约 212MB） |
| `pnpm run build` | 前端产物 |

打包完整流程见 `scripts/`：

```
build-runtime.ps1    构建内置运行时（Python 3.13 + cu128，约 7.6GB）
stage-runtime.ps1    运行时/上游/资产 staged 到 src-tauri/resources
verify-dlls.ps1      校验关键 DLL 与清单哈希
release.ps1          SHA256SUMS / SBOM / licenses / 源码归档（-WithRuntime 打包运行时，>2GB 会被 NSIS 拒绝）
install-runtime.ps1  把 runtime-*.zip 解压到数据目录
```

## 运行时与 Python 隔离

软件只使用自带运行时，**任何情况下都不会调用系统 Python**：

- 找不到内置运行时会报 `SVC-5001`；
- 子进程不继承宿主 `PYTHONPATH`，并设置 `PYTHONNOUSERSITE=1`；
- CPU 推理可用（torch 自动回落），CPU 训练不支持（上游 `train.py` 硬性要求 CUDA）。

## 目录

```
src/             前端（features 按页面，lib 放 api/ipc）
src-tauri/       Rust 核心（commands / core / infra / bridge）
worker/          Python 执行层（svc_bridge）
vendor/          so-vits-svc 4.1-Stable 源码（AGPL-3.0，运行时依赖）
scripts/         构建、质量门、打包、校验
perf/            性能基线（入库，回归即阻断）
（需求 / 验收 / 证据文档不入库，仅在开发机本地保留）
```

## 合规

本项目包含 [so-vits-svc](https://github.com/svc-develop-team/so-vits-svc) 4.1-Stable（AGPL-3.0），源码随仓库提供。
分发安装包时必须随附完整对应源码与构建方式；仓库不内置任何第三方模型权重。
首次启动需完成 AGPL、使用规约、数据集授权、真人声音告知四项确认。

## 状态

- 验收台账：只在开发机本地保留（`docs/` 已列入 .gitignore）
- 质量门：`cargo test 109 / pytest 100 / vitest 35 / clippy 0`
- 已知限制：CPU 训练不支持；单安装包无法内含 7.6GB 运行时（NSIS 32 位 2GB 上限），故运行时单独分发。
