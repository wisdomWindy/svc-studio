# Plan：SVC Studio（Tauri 2 + Rust 内核 + React + Python worker）

| 项 | 值 |
| --- | --- |
| 版本 | v1.0（执行契约） |
| 上游 | [spec](../spec/spec.md)（已批准）、[架构](../design/architecture-design.md)、[页面设计](../design/page-design.md)、[PRD](../PRD.md) |
| 需求 | [requirements](../requirement-splitting/requirements.md)（44 条，P0=12）、[矩阵](../requirement-splitting/traceability.md)（49/49） |
| 质量档 | 见 §7；任何一项不达标即阻断发布 |
| 排期 | **无日期**；只按 WP 依赖与出口门禁推进 |
| 状态 | 待批准（框架自动审批） |

---

## 1. 交付结构（仓库根 = 本工作区）

```
voice-generator/
├─ package.json  pnpm-lock.yaml  tsconfig.json  vite.config.ts  index.html
├─ tailwind.config.ts  postcss.config.cjs  .npmrc  .gitignore  .editorconfig
├─ src/                        React 呈现层（无 fs / 无 shell 权限）
│  ├─ main.tsx  App.tsx  router.tsx  styles/index.css
│  ├─ components/ui/          Button/Field/Modal/Toast/Tooltip/VirtualList/Waveform/...
│  ├─ features/{shell,onboarding,projects,dataset,preprocess,training,inference,models,tasks,settings}/
│  └─ lib/{api.ts,ipc.ts,events.ts,perf.ts,audio.ts,format.ts}
├─ src-tauri/                  Rust 核心层
│  ├─ Cargo.toml  tauri.conf.json  build.rs  capabilities/default.json  icons/
│  └─ src/{main.rs,lib.rs,commands/,core/,infra/,bridge/}
├─ worker/                     Python 执行层（包名 svc_bridge）
│  ├─ pyproject.toml  svc_bridge/{__init__,__main__,rpc,exit_codes,commands/,adapters/}
│  └─ tests/
├─ vendor/so-vits-svc/         4.1-Stable 锁定副本（WP3 落地）
├─ runtime/                    构建产物，不入库（WP9 生成）
├─ scripts/                    构建/校验/基准脚本
├─ perf/baseline.json          性能基线（入库，回归即阻断）
└─ docs/requests/intent_770b99fe990a4a0d/   本请求工件
```

## 2. 工作包拆分与依赖

| WP | 名称 | 依赖 | 出口门禁（可执行证据） |
| --- | --- | --- | --- |
| WP0 | 工程基座与质量门 | — | `pnpm typecheck` / `cargo clippy` / `pytest` 全绿；`pnpm tauri dev` 起窗口；`perf/` 骨架产出基线 JSON |
| WP1 | 存储与领域内核 | WP0 | 迁移到 v1 可空跑；`projects/models/jobs` 仓储单测；崩溃后恢复用例 |
| WP2 | 进程宿主与协议 | WP1 | fake worker 三用例：正常/崩溃/取消（5s→2s）；行缓冲与背压测试；Job Object 杀孙进程 |
| WP3 | 环境与资产 | WP2 | doctor 9 项 + 2 附加；离线导入；篡改拒绝；诊断包白名单扫描 |
| WP4 | 数据集与 QC | WP3 | 30min 集 QC 与 ffprobe 误差 ≤1%；切片 5–15s；阈值断言脚本 |
| WP5 | 预处理与配置 | WP4 | 三步链路 + 计数 0 差异；Schema；`duration` 断言；幂等重跑 |
| WP6 | 训练 | WP5 | 冒烟 100 步 ≤10min；续训步数连续；取消 2s；OOM 自愈一次；模型登记 |
| WP7 | 推理与模型库 | WP2/WP3 | 单条指标达标；外部模型 7 步判定；批次目录隔离；压缩/ONNX/混合 |
| WP8 | 前端 8 页与体验 | WP1/WP2（契约 mock 可先并行） | 冷启动/路由/列表/波形预算；无障碍走查；错误码 12 类文案呈现 |
| WP9 | 打包发布 | WP3–WP8 | 干净 VM 安装、离线全流程、卸载残留 0、体积 ≤2.2GB、源码归档可复现构建 |

## 3. 函数级任务分解

### WP0 工程基座

1. `package.json`：`pnpm@11`、node ≥22；deps：`react@19`、`react-dom`、`react-router@7`、`zustand`、`@tanstack/react-virtual`、`wavesurfer.js@7`、`clsx`、`tailwind-merge`、`class-variance-authority`、`@radix-ui/react-{dialog,tooltip,dropdown-menu,switch,checkbox,progress,tabs}`、`sonner`、`lucide-react`；dev：`vite@7`、`typescript@5`、`tailwindcss@3`、`vitest`、`@testing-library/react`、`jsdom`、`@tauri-apps/cli@2`。
2. `src-tauri/Cargo.toml`：`tauri@2`（features: `protocol-asset`）、`serde/serde_json`、`tokio`（rt-multi-thread, process, sync, time, fs, io-util）、`rusqlite`（bundled）、`sha2`、`hex`、`ed25519-dalek`、`uuid`、`thiserror`、`tracing`/`tracing-subscriber`、`sysinfo`、`windows`（JobObject/JobObjectExtendedLimitInformation）、`reqwest`（rustls, stream）、`notify`。
3. `src-tauri/tauri.conf.json`：identifier `com.svcstudio.app`；`app.windows[0]` 1180×720 min 1180×720；`bundle.targets=["nsis"]`；`resources` 由 `scripts/stage-runtime.ps1` 在打包前填充（`runtime/`、`vendor/so-vits-svc`、`assets/manifest.json`）。
4. `capabilities/default.json`：只授予 `core:default`、`dialog:allow-open`、`dialog:allow-save`、`notification:default`、`window-state:default` + 自定义命令。
5. `src/lib/api.ts`：`invoke` 的唯一包装（超时、错误归一为 `AppError`、dev 下日志）。
6. `scripts/`：`dev.ps1`、`check.ps1`（typecheck+clippy+ruff+pytest+vitest）、`bench.ps1`（跑 `perf/bench.ts` 输出 JSON）、`stage-runtime.ps1`（生成 `runtime/` 与 DLL 清单）。

**M0 出口**：窗口能起、`ping` 往返、`check.ps1` 全绿、`perf/baseline.json` 有第一版数字。

### WP1 存储与领域内核

1. `src-tauri/src/infra/db.rs`
   - `pub fn open(path: &Path) -> Result<Connection>`（WAL、foreign_keys、busy_timeout=5000、`PRAGMA user_version`）
   - `pub fn migrate(conn: &mut Connection) -> Result<u32>`：`migrations/V001__init.sql … V00N`，每步事务 + 备份 `studio.db.bak.<v>`（spec §15）
2. `migrations/V001__init.sql`：按 [附录 C](../prd/appendix-c-data-model.md) C.1 建 18 张表 + 索引 + `CHECK` 约束（`jobs.status` 枚举、`models.status` 枚举）。
3. `core/models.rs`：`Project/Job/JobEvent/ModelCard/Checkpoint/Asset/DatasetSummary`（serde 双向，字段与 TS `types.ts` 一一对应）。
4. `core/repo/{projects,jobs,models,datasets,settings}.rs`：纯函数式查询 + 事务；禁止在 repo 外拼 SQL。
5. `infra/fs.rs`：`atomic_write(path, bytes)`（tmp→fsync→rename）、`ensure_dir`、`safe_join(root, rel)`（拒绝 `..`、盘符、UNC）。
6. `core/ids.rs`：job id `job_<ulid>`、model id `mdl_<ulid>`（ULID 单调，便于排序）。

**TDD**：迁移幂等、并发写 SQLITE_BUSY 重试、`safe_join` 路径穿越 12 例、崩溃后 `interrupted` 对账。

### WP2 进程宿主与协议

1. `bridge/protocol.rs`：`JobRequest{job_id,kind,params}`、`event::*`（`stage/progress/log/result/error`）、`serde(deny_unknown_fields)`。
2. `bridge/host.rs`：`ProcessHost::spawn(runtime_python, worker_main, req) -> JobHandle`；stdout 行读取（`BufReader::lines`，限长 64KB）；stderr 单独线程 → `log` 事件；stdin 写 `stop`/`cancel`。
3. `infra/job_object.rs`：`kill_tree(pid)`（JobObject + `TerminateJobObject`），父进程退出即关句柄。
4. `core/scheduler.rs`：GPU 门（`tokio::sync::Semaphore` 容量 1）+ 优先级队列（用户交互 > 后台）；`queued→running` 迁移只在此处发生。
5. 背压：事件合流 16ms；日志行 >200/s 时降采样为 `log_batch`。
6. `worker/svc_bridge/rpc.py`：`emit(event)`（`json.dumps` + `flush`，单线程锁）、`read_control()`、`main()` 分发到 `commands/*`。

**TDD**：fake worker（`tests/fixtures/fake_worker.py`）模拟正常/崩溃/挂起/输出洪水；断言 5s→2s 取消、无僵尸、事件不丢。

### WP3 环境与资产

1. `worker/svc_bridge/commands/doctor.py`：9 项 + `base_weights` 附加；输出 `DoctorReport{items:[{id,state,message,fix,detail}]}`；`--json` 由 Rust 侧包装（B1 用 `svcstudio --doctor --json`）。
2. `src-tauri/src/commands/bootstrap.rs`：`bootstrap()` 返回 `{app_version, runtime_version, db_version, paths, webview2, gpu}`。
3. `core/assets/manifest.rs`：解析 `assets/manifest.json`、验签（Ed25519，公钥 `include_bytes!`）、sha256 双算。
4. `commands/assets.rs`：`list_assets`、`import_asset_dir`、`download_asset`（Range 续传 + 镜fallback + 3 次退避）、`verify_asset`。
5. `core/diagnostics.rs`：白名单打包（`doctor.json`/`versions.json`/`tail-2000.txt`/`config-snapshot.json`/`manifest-check.json`）+ 打包前内容扫描（扩展名与魔数双检）。
6. `vendor/so-vits-svc/`：`scripts/vendor-upstream.ps1` 克隆 4.1-Stable 到 `vendor/so-vits-svc`，记录 commit SHA 到 `vendor/UPSTREAM.json`。

**TDD**：篡改 1 字节拒绝、断网导入、诊断包不含音频（扫描断言）、清单签名失败进入只读模式。

### WP4 数据集与 QC

1. `worker/svc_bridge/commands/dataset.py`：`import_dataset`（结构校验、非 wav 列表、0 字节/不可解码）、`qc`（7 指标，ffprobe 抽样对账）、`slice`（5–15s，300/100/50ms）、`resample`（44100/mono/-1dBFS 峰值保护，`--skip_loudnorm` 可关）、`denoise`（afftdn，默认关）。
2. `core/dataset/fingerprint.rs`：`sha256(sorted(rel,size,sr,dur))`。
3. `commands/dataset.rs`：`import_dataset_preview`、`run_qc`、`apply_slice`、`resolve_issue`、`get_qc_report`。
4. 阈值断言：`worker/svc_bridge/commands/qc_assert.py`（C5 门禁，被 Rust 调用，非前端判断）。

### WP5 预处理与配置

1. `worker/svc_bridge/commands/preprocess.py`：三步顺序执行（`resample.py` → `preprocess_flist_config.py` → `preprocess_hubert_f0.py`），每步独立子进程、逐条幂等跳过、写 `preprocess_manifest.json`。
2. `commands/preprocess.rs`：`start_preprocess`、`preprocess_diff`（计数比对）、`retry_single_file`。
3. `core/config/schema.rs` + `schemas/config.schema.json`、`diffusion.schema.json`：白名单字段、范围、只读字段、行号定位错误。
4. `commands/config.rs`：`read_config`、`validate_config`、`save_config`（备份 + diff 返回）、`assert_preconditions`（10 条硬断言见附录 A.7）。

### WP6 训练

1. `core/train/recommend.rs`：显存→batch/duration/num_processes 表（PRD §5.6），带 `reason` 字段。
2. `worker/svc_bridge/commands/train.py`：`smoke`（复制临时 config：`eval_interval=50`/`log_interval=10`/`keep_ckpts=2`，`-m _smoke`，100 步优雅停）、`main`（`-m 44k`，自动续训）、`diffusion`、`cluster`、`index`。
3. `core/train/supervisor.rs`：日志/事件解析（`Losses: [...], step: N` + `events/`）、进度、ETA、VRAM 采样（5s）、OOM 识别（一次降档）、NaN 停机、checkpoint 发现与登记。
4. `core/train/preflight.rs`：新建 vs 续训判定（`logs/44k` 有未登记 checkpoint → 强制归档或续训）；端口探测（`config.train.port` 占用则 +1）。
5. `commands/train.rs`：`train_recommend`、`train_start{smoke|main|diffusion|cluster|index}`、`train_stop`、`train_resume`、`list_checkpoints`、`register_model`。

### WP7 推理与模型库

1. `core/model/compat.rs`：7 步判定（封装兼容、config 解析、4.0 补 `speech_encoder`、采样率、实例化、逐键形状、`emb_g` 对 `spk`），输出 `CompatReport`（`verdict/missing_keys/inferred`）。
2. `core/model/importer.rs`：上传六步（预检磁盘 → staging 边复制边 sha256 → 复算 → 判定 → 原子落位 `imports/<slug>/model` → 登记）；完整性前置（1:1 才复制）；`_unrecognized/` 7 天。
3. `worker/svc_bridge/adapters/svc_infer.py`：直接调 `Svc.slice_inference`（不跑 `inference_main.py`），参数映射表见附录 A.5；whisper 自动 `clip=25/lg=1`；输出写批次目录 + `report.json`。
4. `commands/inference.rs`：`start_inference`（单条/批量）、`retry_failed`、`list_batches`、`open_batch_dir`、`export_metadata`。
5. `commands/models.rs`：`list_models`、`set_default_model`、`compress_model`、`onnx_export`、`export_package`、`import_package`、`delete_model`（占用检查）。
6. `core/model/mix.rs`：静态混合（凸/线性 + 结构校验）、动态轨道校验（首 0、末 1、首尾相接、凸组合归一）。

### WP8 前端

1. `router.tsx`：8 路由 + 懒加载 + 预取（hover/intent）；`features/shell/AppShell.tsx`（侧栏 + 流水线条 + 任务浮层 + toast）。
2. `lib/events.ts`：Rust `Channel` 订阅 → zustand store（`jobsStore` 归一化表 + 选择器）；`useJob(jobId)` 只订阅该 job 的切片。
3. 虚拟列表：`components/ui/VirtualList.tsx`（TanStack Virtual，固定行高 40，overscan 10）。
4. 波形：`components/ui/Waveform.tsx`（wavesurfer 7，peaks 预先由 Rust 计算，避免前端解码整段）。
5. 上传区：`features/models/Dropzone.tsx`（Tauri `onDragDropEvent`，`dragEnter/Leave` 计数、`getCurrentWebview().onDragDropEvent`）、内联来源勾选、两个弹窗。
6. 每页接 `lib/api.ts`，状态机与文案严格按页面设计 §6 与错误码附录 B。
7. `lib/perf.ts`：`markRoute`、`measureLongTask`（>50ms 上报 console 与 `perf` 事件）。

### WP9 打包发布

1. `scripts/build-runtime.ps1`：下载/校验 CPython 3.8.9 embeddable + 锁定 wheel（`requirements.lock.txt`）→ `runtime/`；`--verify` 跑 `python -c "import torch, fairseq"`。
2. `scripts/stage-runtime.ps1`：复制 `runtime/`、`vendor/`、`assets/` 到 `src-tauri/resources/`，生成 `DLLS.json`（`dumpbin` 或 `pefile` 扫描）。
3. `scripts/verify-dlls.ps1`：对比 `DLLS.json` 与安装产物，缺失即非零退出。
4. `src-tauri/nsis/hooks.nsh`：WebView2 检测 + 离线安装包调用；卸载保留用户数据选项。
5. `scripts/release.ps1`：构建 → 签名（可选）→ 生成 `SBOM.json`（CycloneDX）、`licenses/`、`source-<ver>.zip`、`SHA256SUMS`。
6. `scripts/verify-clean-vm.ps1`：干净机脚本化验收（doctor → 离线导入 → 冒烟 → 推理 → 卸载快照对比）。

## 4. 契约同步规则

- TS 类型由 `src-tauri` 的 serde 结构**手工镜像**（不引 tauri-specta，避免代码生成链）：`src/types.ts` 顶部注释指向 Rust 定义位置，`check.ps1` 里跑 `scripts/check-types.ps1` 比对 command 列表与 TS 声明（正则提取，防漏）。
- JSON 形状（qc/preprocess/train/inference report）以附录 C.4 为准，Rust 与 Python 双侧各有一份 schema 测试。

## 5. 状态机实现点

| 迁移 | 唯一实现点 | 约束 |
| --- | --- | --- |
| `queued→running` | `core/scheduler.rs` | 只有拿到 GPU 门才迁移 |
| `running→stopping→stopped` | `core/train/supervisor.rs` | 只对 `train/diffusion` |
| `running→cancelled` | `commands/jobs.rs::cancel` | 先 `kill_tree` 再改状态 |
| `running→failed` | `bridge/host.rs` 退出回调 | 必带 `exit_code` 与最后 200 行 |
| `running→interrupted` | `commands/bootstrap.rs::reconcile` | 启动时以 pid 存在性为准 |

## 6. 测试计划

| 层 | 工具 | 必须覆盖 |
| --- | --- | --- |
| Rust | `cargo test` + `proptest` | 迁移、路径穿越、sha256/验签、协议解析 fuzz、Job Object 杀树（Windows-only） |
| Python | `pytest` | QC 指标口径、切片边界、config 断言、7 步判定（三个 fixture 模型）、批次目录 |
| 前端 | `vitest` + RTL | 状态机渲染、上传三态、错误三段式、虚拟列表滚动、a11y（roles/labels） |
| 端到端 | `scripts/e2e/*.ps1` | 冒烟链路、取消链路、离线链路（无网卡模拟） |
| 性能 | `perf/bench.ts` + `perf/*.ps1` | 启动、路由、列表 1w/10w、IPC 往返、波形加载、长任务帧延迟 |
| 声音 | `worker/tests/audio_metrics.py` | 时长/峰值/削波/包络相关性（F1/F2/F8 判据） |

## 7. 质量门（`scripts/check.ps1` 全绿才算一次"可提交"）

1. `pnpm typecheck`（`tsc --noEmit --strict`，`noUncheckedIndexedAccess`）
2. `pnpm lint`（eslint：`@typescript-eslint/no-explicit-any` error、`react-hooks`）
3. `cargo fmt --check` + `cargo clippy --all-targets -- -D warnings`
4. `ruff check` + `mypy --strict worker/svc_bridge`
5. `pytest worker/tests -q`
6. `vitest run`
7. `node scripts/assert-budget.mjs`（`dist/assets/*.js` 首屏 gzip ≤180KB）
8. `node scripts/check-bundle.mjs`（禁止 `eval`、远程 URL、`any` 泄漏）

## 8. 执行顺序

```
WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6 → WP7 → WP8 → WP9
                └──────── WP8 从 WP1 起可用契约 mock 并行 ────────┘
```

每个 WP 完成即写 `execution/changelog.md` 一条（含证据命令与输出摘要），并更新 `plan/task-board.md` 状态。

## 9. 回退策略

| 失败点 | 回退 |
| --- | --- |
| 内置 runtime 无法被 resources 加载（A2） | 改外部 sidecar 目录 + 首次启动解压；仍失败则改回 PyInstaller onedir（ADR-001 附条件） |
| Job Object 杀不掉孙进程（A9） | 改 `CREATE_NEW_PROCESS_GROUP` + `taskkill /T`，并加进程名白名单兜底 |
| bridge 无法脱离 `inference_main.py`（A3） | 回退"脚本 + cwd 注入"，代价是放弃部分进度事件 |
| WebView2 离线包失败（A8） | 安装器内置 Evergreen 引导 + 在线兜底，并在 doctor 标记 |
| 性能不达标 | 按 §7 阻断发布，先修再谈功能 |
