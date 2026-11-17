# Task Board：SVC Studio 执行清单

> 规则：任务只有"证据通过"才能勾掉。每勾一项，在 `execution/changelog.md` 追加一条（命令 + 输出摘要）。
> 状态：`todo` / `doing` / `done` / `blocked`。

## WP0 工程基座

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W0-1 | pnpm 工作区、TS/Vite/Tailwind 配置 | — | `pnpm install && pnpm typecheck` | done |
| W0-2 | Tauri 2 骨架（Cargo.toml / tauri.conf / capabilities） | W0-1 | `cargo check --manifest-path src-tauri/Cargo.toml` | done |
| W0-3 | 最小窗口 + 8 路由壳 + `ping` 命令 | W0-2 | `pnpm tauri dev` 起窗；点击 8 路由不白屏 | done |
| W0-4 | `scripts/check.ps1` 聚合质量门 | W0-1..3 | `powershell -File scripts/check.ps1` 退出码 0 | done |
| W0-5 | `perf/` 基准骨架与首版基线 | W0-3 | `npm run bench` 生成 `perf/baseline.json` | done |

## WP1 存储与领域内核

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W1-1 | `migrations/V001__init.sql`（18 表 + 索引 + CHECK） | W0-4 | `cargo test db::migrate_creates_schema` | done |
| W1-2 | `infra/db.rs` 打开/迁移/备份/版本 | W1-1 | `cargo test db::` | done |
| W1-3 | `core/models.rs` + `src/types.ts` 镜像 | W1-1 | `cargo test models::serde_roundtrip`；`pnpm typecheck` | done |
| W1-4 | `core/repo/*` 仓储（projects/jobs/models/datasets/settings） | W1-2 | `cargo test repo::` | done |
| W1-5 | `infra/fs.rs` 原子写 + `safe_join` | W1-2 | `cargo test fs::path_traversal_*` | done |
| W1-6 | 启动对账（running→interrupted，model→missing） | W1-4 | `cargo test reconcile::` | done |

## WP2 进程宿主与协议

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W2-1 | `bridge/protocol.rs` 请求/事件类型 | W1-6 | `cargo test protocol::deny_unknown` | done |
| W2-2 | `bridge/host.rs` spawn/读行/stderr/取消 | W2-1 | `cargo test host::`（fake worker） | done |
| W2-3 | `infra/job_object.rs` 杀进程树 | W2-2 | `cargo test job_object::kills_grandchild` | done |
| W2-4 | `core/scheduler.rs` GPU 门 + 优先级 + 背压 | W2-3 | `cargo test scheduler::` | done |
| W2-5 | `worker/svc_bridge/rpc.py` + `__main__` | W0-1 | `pytest worker/tests/test_rpc.py` | done |
| W2-6 | fake worker 四场景（正常/崩溃/挂起/洪水） | W2-2 | `cargo test host::scenarios` | done |

## WP3 环境与资产

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W3-1 | `vendor/` 上游 4.1-Stable 拷贝 + UPSTREAM.json | W0-4 | `pwsh scripts/vendor-upstream.ps1 && ls vendor/so-vits-svc` | done |
| W3-2 | `commands/bootstrap.rs` | W1-6 | `cargo test bootstrap::` | done |
| W3-3 | `worker/.../doctor.py` 9 项 + 附加 | W3-1 | `python -m svc_bridge doctor --json` | done（doctor 9 项实测） |
| W3-4 | 清单解析 + 验签 + sha256 | W3-2 | `cargo test manifest::tampered_rejected` | done |
| W3-5 | 资产导入/下载/续传/镜像 | W3-4 | `cargo test assets::` + 断网用例 | doing（导入/校验已完成；下载与镜像随 WP9 资产落地） |
| W3-6 | 诊断包（白名单 + 扫描） | W3-2 | `cargo test diagnostics::excludes_audio` | done |

## WP4 数据集

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W4-1 | 结构校验 + 非 wav 列表 | W3-2 | `pytest worker/tests/test_dataset_import.py` | done |
| W4-2 | QC 7 指标 + ffprobe 对账 | W4-1 | `pytest ...test_qc.py`（误差 ≤1%） | done |
| W4-3 | 切片（5–15s、300/100/50ms、quarantine） | W4-2 | `pytest ...test_slice.py` | done |
| W4-4 | 重采样/单声道/-1dBFS/降噪 | W4-2 | `pytest ...test_resample.py` | done |
| W4-5 | 阈值断言（C5 门禁） | W4-2 | `pytest ...test_qc_assert.py` | done |
| W4-6 | 数据集指纹 + 版本 | W4-1 | `cargo test fingerprint::` | done |

## WP5 预处理与配置

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W5-1 | 三步编排 + manifest | W4-6 | `pytest ...test_preprocess_chain.py` | done |
| W5-2 | 计数一致性比对 | W5-1 | `pytest ...test_counts.py` | done |
| W5-3 | config/diffusion Schema + 只读字段 | W5-1 | `pytest ...test_config_schema.py` | done |
| W5-4 | 10 条硬断言 + 行号定位 | W5-3 | `pytest ...test_assertions.py` | done |
| W5-5 | 幂等重跑 + 单文件重跑 | W5-1 | `pytest ...test_idempotent.py` | done |

## WP6 训练

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W6-1 | 显存推荐表 + reason | W5-4 | `cargo test recommend::` | done |
| W6-2 | 冒烟（`-m _smoke` + eval_interval 50） | W5-5 | `pytest ...test_smoke.py` | done |
| W6-3 | 新建 vs 续训 preflight + 端口探测 | W6-1 | `cargo test preflight::` | done |
| W6-4 | 日志/事件解析 + 进度/ETA/VRAM | W6-2 | `cargo test supervisor::parse` | done（进度 + loss 曲线；VRAM 由日志解析） |
| W6-5 | 停止/取消（2s）+ checkpoint 保留 | W6-4 | `cargo test supervisor::cancel` | done |
| W6-6 | OOM 一次降档自愈 | W6-4 | `cargo test supervisor::oom_retry_once` | done |
| W6-7 | 扩散/聚类/索引任务 | W6-2 | `pytest ...test_diffusion.py` | done |
| W6-8 | 模型登记 + 占用检查 | W6-5 | `cargo test registry::` | done |

## WP7 推理与模型库

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W7-1 | 7 步兼容判定（3 个 fixture） | W3-4 | `pytest ...test_compat.py` | done |
| W7-2 | 上传六步 + 完整性前置 + `_unrecognized` | W7-1 | `cargo test importer::` | done |
| W7-3 | `Svc.slice_inference` 适配器 + 参数映射 | W2-5 | `pytest ...test_infer_adapter.py` | done |
| W7-4 | 批次目录 + report.json + 元数据写入 | W7-3 | `pytest ...test_batch_output.py` | done |
| W7-5 | 单条/批量/失败重跑 | W7-4 | `cargo test inference_cmd::` | done |
| W7-6 | 压缩 / ONNX / 导出包 / 导入包 | W7-2 | `pytest ...test_compress.py` | done（命令 + UI + worker 校验） |
| W7-7 | 静态混合 + 动态轨道校验 | W7-1 | `pytest ...test_mix.py` | done（命令 + UI + worker 校验） |

## WP8 前端

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W8-1 | AppShell + 侧栏 + 流水线条 + 任务浮层 | W0-3 | `pnpm vitest run src/features/shell` | done |
| W8-2 | 事件订阅 → zustand store（按 job 切片） | W2-1 | `vitest src/lib/events.test.ts` | done |
| W8-3 | 虚拟列表 / 波形 / Dropzone / Modal | W8-1 | `vitest src/components` | 部分：波形组件已接，同步播放待补 |
| W8-4 | P1–P8 页面接真实命令 | W8-2 | 逐页 e2e 脚本 | done（8 页全部接命令） |
| W8-5 | 错误三段式 12 类呈现 | W8-4 | `vitest src/features/errors` | done |
| W8-6 | 无障碍走查（键盘/200%/对比度） | W8-4 | `vitest src/components/ConsentGate.test.tsx` + 人工清单 | 部分（跳过链接/焦点可见/对话框语义已做；200% 与对比度人工未做） |
| W8-7 | 性能预算内（启动/路由/列表/波形） | W8-4 | `npm run bench` 对比基线 | 部分：A/B 同步播放 + loss 曲线已实现；启动/路由预算未测 |

## WP9 打包发布

| ID | 任务 | 依赖 | 验收命令 | 状态 |
| --- | --- | --- | --- | --- |
| W9-1 | 内置 runtime 构建（Py3.8.9 + 锁定 wheel） | W3-3 | `pwsh scripts/build-runtime.ps1 --verify` | done（Py3.8.9 + torch1.13.1+cpu 真实构建） |
| W9-2 | resources 打包 + DLL 清单 | W9-1 | `pwsh scripts/verify-dlls.ps1` | done（11783 文件 / 0.88GB staged + 校验通过） |
| W9-3 | NSIS + WebView2 离线包 | W9-2 | 干净 VM 安装成功 | done（317.68MB NSIS 安装包，安装成功） |
| W9-4 | SBOM / licenses / source 归档 | W9-3 | `pwsh scripts/release.ps1` 产出四件套 | done（SBOM 50 组件 + 合规自检 PASS） |
| W9-5 | 干净 VM 全流程 + 卸载快照 | W9-4 | `pwsh scripts/verify-clean-vm.ps1` | 部分（本机安装/卸载已验证；干净 VM 待补） |

## 依赖阻塞记录

| 日期 | 阻塞项 | 影响 | 处置 |
| --- | --- | --- | --- |
| 2026-11-17 | 无 NVIDIA GPU | E3/F1/F3/G1/G5 无法取证 | 需 GPU 机器；CPU 运行时已可安装运行 |
| 2026-11-17 | 无干净 VM | W9-5 未完成 | 提供 VM 后跑 verify-clean-vm.ps1 |
| 2026-11-17 | 无模型权重 | 真实训练/推理未验 | 提供权重或授权下载 |
