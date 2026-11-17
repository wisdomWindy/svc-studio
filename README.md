# SVC Studio

so-vits-svc 4.1-Stable 的 Windows 桌面产线：导入数据 → 一键预处理 → 训练监控 → 试听导出。
**SVC（音色转换），不是 TTS。**

| 层 | 技术 |
| --- | --- |
| 呈现 | React 19 + TypeScript + Vite + Tailwind（WebView2） |
| 核心 | Rust + Tauri 2（任务状态机、SQLite、进程树、原子写、sha256） |
| 执行 | 内置 CPython 3.8.9 + `worker/svc_bridge`（预处理/训练/推理） |

## 开发

```powershell
pnpm install            # 依赖
pnpm dev                # 浏览器预览（无 Tauri 后端，页面可看）
pnpm tauri dev          # 桌面窗口（需要 Rust + MSVC Build Tools）
pnpm typecheck          # tsc --noEmit
pnpm test               # vitest
python -m pytest worker/tests -q
pwsh scripts/check.ps1  # 质量门：类型 / 测试 / 体积 / clippy
npm run bench           # 产物体积基线 → perf/baseline.json
```

## 目录

```
src/             前端（features 按页面，lib 放 api/ipc/perf）
src-tauri/       Rust 核心（commands / core / infra / bridge）
worker/          Python 执行层（svc_bridge）
scripts/         构建、质量门、打包、校验
perf/            性能基线（入库，回归即阻断）
docs/requests/   本需求的 PRD / spec / 架构 / plan / 验收证据
```

## 状态

M0（工程基座）：窗口骨架、8 路由、IPC 握手、质量门、性能基线。
功能按 `docs/requests/intent_770b99fe990a4a0d/plan/task-board.md` 推进。

## 合规

本项目包含 [so-vits-svc](https://github.com/svc-develop-team/so-vits-svc)（AGPL-3.0）。分发安装包时必须随附对应源码与构建方式；不内置任何第三方模型权重。首次启动需完成 AGPL、使用规约、数据集授权、真人声音告知四项确认。
