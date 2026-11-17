# 架构设计：SVC Studio（Tauri 2 + Rust 核心 + React 前端 + Python worker）

| 项 | 值 |
| --- | --- |
| 上游 | [spec](../spec/spec.md)（已批准）、[页面设计](page-design.md)、[PRD](../PRD.md) |
| 决策记录 | [ADR-001 UI/运行时](../adr/ADR-001-ui-and-runtime.md) · [ADR-002 任务模型](../adr/ADR-002-job-model.md) · [ADR-003 资产](../adr/ADR-003-assets-offline.md) |
| 栈基线 | Tauri 2 · Rust stable · React 19 + TS 5 + Vite · Tailwind CSS · rusqlite · tokio · CPython 3.8.9（内置）· torch 1.13.1(cu117)/2.0.1(cu118) |
| 规模目标 | Rust ≤ 6k 行、TS ≤ 8k 行、Python bridge ≤ 3k 行（不含 vendored 上游） |

## 1. 架构目标与不可协商项

1. **计算内核必须是 Python**：不重写 VITS/NSF-HiFiGAN/ContentVec，复用上游权重与基线。
2. **UI 与计算物理隔离**：WebView 无文件/进程权限；torch 只在 worker 里存在。
3. **状态只有一个 owner**：任务状态机与 DB 只在 Rust；前端只持有"投影"。
4. **可离线、可强杀、可续训**：进程树用 Job Object；原子写；启动对账。
5. **可测**：Rust domain 与 Python bridge 的参数装配/事件解析不依赖 UI、不依赖真实 GPU。

## 2. 脚手架决策（greenfield 强制项）

| 候选 | 结论 | 说明 |
| --- | --- | --- |
| `create-tauri-app`（官方，React+TS 模板） | **采用** | 生成 `src/` + `src-tauri/` + capabilities + 图标管线，直接对齐 Tauri 2 约定 |
| 自建 Vite + 手写 `tauri.conf.json` | 否决 | 无收益，且容易漏 capabilities/updater 配置 |
| Next.js / SSR 框架 | 否决 | 桌面端无 SSR 需求，反而引入 Node 服务端概念与额外攻击面 |
| 组件库（MUI/Ant/shadcn 全量） | **局部采用** | 只用 shadcn 风格的无样式 primitives（Radix）+ Tailwind；不引入整套主题系统（会与 §9 的视觉 token 冲突） |
| 状态库 | **zustand** | 比 Redux 轻；比 Context 更适合"任务列表高频更新"；selector 切片订阅避免整树重渲染 |

**偏差记录**：`create-tauri-app` 生成的 `greet` 示例命令、默认窗口尺寸、示例图标全部删除；`main.rs` 里不留模板注释。

## 3. 目录结构

```
voice-generator/
├─ package.json  pnpm-lock.yaml  vite.config.ts  tailwind.config.ts  tsconfig.json  index.html
├─ contracts/                        # 跨语言契约（单一事实源）
│  ├─ job-events.schema.json         # worker → Rust → 前端 的事件/命令 JSON Schema
│  └─ commands.ts                    # 由 schema 生成的 TS 类型（CI 校验不漂移）
├─ src/                              # 前端（渲染层，无业务规则）
│  ├─ app/App.tsx  app/shell.tsx  app/providers.tsx
│  ├─ pages/Project  pages/Dataset  pages/Preprocess  pages/Train
│  │        pages/Inference  pages/Models  pages/Jobs  pages/Settings
│  ├─ features/                      # 按业务能力切，非按技术切
│  │   browse/  dataset/  preprocess/  train/  infer/  models/  jobs/  doctor/  assets/
│  ├─ components/ui/                 # Button Dialog Tabs Table Toast Progress Tooltip
│  ├─ components/domain/             # PipelineSteps ModelCard DropZone ABPlayer LossChart QcReport JobRow
│  ├─ ipc/                           # client.ts（invoke 包装）channel.ts（流订阅）errors.ts
│  ├─ stores/                        # jobs project selection settings toasts
│  └─ lib/                           # format audio theme i18n-zh
├─ src-tauri/
│  ├─ Cargo.toml  build.rs  tauri.conf.json  capabilities/default.json  icons/
│  ├─ src/main.rs  src/lib.rs        # 注册 commands / plugins / 启动对账
│  ├─ src/commands/                  # 薄适配层：校验入参 → 调 service → 返回 DTO
│  ├─ src/domain/                    # 纯逻辑：job 状态机、模型兼容判定、路径规则、错误枚举
│  ├─ src/services/                  # 用例：job_manager scheduler importer verifier doctor
│  │                                 #       registry exporter reporter diagnostics
│  ├─ src/infra/                     # db(+migrations) process job_object fsx hashing logs paths
│  └─ src/events/                    # hub（Channel 广播） + throttle（100ms 合并）
├─ worker/                           # 计算层（Python 3.8）
│  ├─ worker.py                      # 协议入口：读 stdin 命令，写 stdout 事件
│  ├─ svc_bridge/
│  │   ├─ protocol.py  events.py  paths.py  upstream.py
│  │   ├─ jobs/preprocess.py  jobs/train.py  jobs/infer.py  jobs/import_check.py  jobs/doctor.py
│  │   └─ patch/                     # 对上游的运行时补丁（最小化，逐条记录）
│  └─ tests/
├─ vendor/so-vits-svc-4.1/           # 上游 vendored（只读）+ PATCHES.md
├─ runtime/                          # 构建产物：python-build-standalone 3.8.9 + site-packages（不入 git）
└─ packaging/                        # 安装器脚本、licenses/、sbom 生成、校验和
```

**依赖方向（单向，CI 用 `cargo-deny`/`eslint-plugin-boundaries` 守）**

```
src/ (React)  ──invoke/channel──▶  src-tauri/commands ──▶ services ──▶ domain
                                                          │
                                                          ├─▶ infra（db/process/fs）
                                                          └─▶ worker（spawn + JSONL）
src/ 不得 import src-tauri 的任何 Rust 代码（只经 IPC）；domain 不得 import infra。
```

## 4. 模块所有权

| 模块 | 拥有（可写） | 只读/调用 | 禁止 |
| --- | --- | --- | --- |
| `src/stores/jobs` | 前端任务视图状态（快照、选中、过滤） | `commands::jobs_list`, `channel::job` | 不推导业务状态（状态由 Rust 给） |
| `src/features/*` | 页面局部 UI 状态（表单草稿、折叠） | stores + ipc | 不直连文件、不做校验规则的最终判定 |
| `commands/*` | 入参 DTO 校验、错误映射 | services | 不写 SQL、不 spawn 进程 |
| `services/job_manager` | `jobs` 状态机、`job_events` 批量落库 | `infra::process`, `events::hub` | 不解析上游日志（归 worker） |
| `services/importer` | `import_sessions`、`models(source=imported)` | `infra::hashing/fsx`, worker 的 `import_check` | 不做模型结构判定（归 Python） |
| `services/verifier` | `assets`/`asset_files` 状态 | `infra::hashing` | 不下载（下载在 assets service） |
| `services/doctor` | doctor 结果缓存 | `infra::process`（ffmpeg/nvidia-smi） | 不改资产表 |
| `worker/svc_bridge` | 无 DB；只产出事件与报告文件 | 上游模块、项目工作区 | 不写 SQLite、不碰 UI |
| `vendor/` | 无（只读） | — | 任何直接修改（补丁走 `worker/svc_bridge/patch/` 或 `PATCHES.md`） |

## 5. 关键接口

### 5.1 前端 → Rust（Tauri command，节选）

```ts
// contracts/commands.ts（生成，勿手改）
type JobId = string;

invoke("project_create", { name: string; rootPath: string; speechEncoder: Encoder; f0: F0 }): ProjectDto
invoke("dataset_import", { projectId: string; sourceDir: string; consent: boolean }): JobId
invoke("qc_run", { datasetId: number }): JobId
invoke("preprocess_start", { datasetId: number; opts: PreprocessOpts }): JobId
invoke("train_smoke", { projectId: string; batchSize: number }): JobId
invoke("train_start", { projectId: string; mode: "new" | "resume"; params: TrainParams }): JobId
invoke("infer_start", { modelId: number; inputs: string[]; params: InferParams; outDir: string }): JobId
invoke("model_import_inspect", { paths: string[]; consent: boolean }): ImportInspectionDto
invoke("model_import_commit", { sessionId: string }): ModelDto
invoke("jobs_cancel", { jobId: string; mode: "stop" | "cancel" }): void
invoke("jobs_list", { filter: JobFilter; page: number }): Page<JobRow>
```

流：`channel("job.stream", jobId)` 收 `{ kind: "log" | "progress" | "stage" | "metric" | "artifact" }`，Rust 侧 100ms 合并。

### 5.2 Rust 内部（trait 即端口）

```rust
pub trait ProcessHost: Send + Sync {
    fn spawn(&self, spec: SpawnSpec) -> Result<ChildHandle, HostError>; // 绑定 Job Object
    fn kill_tree(&self, h: &ChildHandle) -> Result<(), HostError>;
}
pub trait Clock: Send + Sync { fn now(&self) -> DateTime<Utc>; }
pub trait Hasher: Send + Sync { fn sha256_file(&self, p: &Path) -> Result<String, HashError>; }

pub struct JobManager<H: ProcessHost, D: JobRepo> { /* … */ }
impl<H: ProcessHost, D: JobRepo> JobManager<H, D> {
    pub fn start(&self, spec: JobSpec) -> Result<JobId, JobError>;
    pub fn stop(&self, id: &JobId, mode: StopMode) -> Result<(), JobError>;
    pub fn on_event(&self, id: &JobId, ev: WorkerEvent) -> Result<(), JobError>;
    pub fn reconcile(&self) -> Result<ReconcileReport, JobError>;   // 启动对账
}
```

测试用 `FakeProcessHost`（可注入"立即退出/OOM/超时/心跳缺失"）+ `FakeClock`，不起真进程。

### 5.3 Rust → worker（JSON Lines，契约在 `contracts/job-events.schema.json`）

见 [spec §6.2](../spec/spec.md)。三条硬规则：
1. stdout 只有 JSON；任何库的 print 都被重定向（ADR-002 §4.1）。
2. 事件带 `seq`（单调递增），Rust 侧检测空洞 → 记 `event_gap` 并降级为日志解析。
3. `result` 必须是最后一条；worker 退出码与 `result.status` 不一致时以退出码为准并告警。

## 6. 并发与进程模型

| 执行体 | 职责 | 约束 |
| --- | --- | --- |
| tokio runtime（多线程） | IPC、调度、事件扇出 | 禁止在 handler 里同步 IO；>16ms 一律 `spawn_blocking` |
| 阻塞池（tokio blocking） | sha256、目录遍历、大文件复制、DB 写 | 池上限 4；大复制按 4MB 块，报告进度 |
| `db` | 单写者（`Mutex<Connection>` + WAL） | 事务批量 100ms 提交；读连接独立 |
| GPU 信号量（1 许可） | 训练/扩散/推理/预处理 | 排队 FIFO；可见"前面还有 N 个" |
| CPU 信号量（2 许可） | QC/切片/导入校验 | 与 GPU 任务并行 |

前端不做任何计算密集的事：波形峰值由 Rust 生成 `peaks.json`（每 10ms 一个 min/max），前端只画。

## 7. 存储设计

- `rusqlite` + `refinery`/手写 migrations；`PRAGMA journal_mode=WAL`、`busy_timeout=5s`、`foreign_keys=ON`。
- 表结构见 [附录 C](../prd/appendix-c-data-model.md)，唯一改动：`jobs.progress` 改为 `progress_json`（结构化：`{stage, step, total, eta_s}`），避免前端解析字符串。
- 写路径：service → `JobRepo`（事务）→ 事件 `job_events` → 提交后 `hub.broadcast`（**先落库再广播**，保证崩溃后不丢事实）。

## 8. 与上游集成（最高风险区）

| 场景 | 方式 | 关键点 |
| --- | --- | --- |
| 预处理三步 | `Command::new(python).arg("resample.py")...current_dir(project)` | 参数全部显式传，不依赖默认值 |
| 训练 / 扩散 | 同上，`-c configs/config.json -m 44k` / `-m _smoke` | 冒烟写独立目录，避免污染（spec F-07） |
| 推理 | **API 级**：`svc_bridge/jobs/infer.py` 直接 `from inference.infer_tool import Svc` 调 `slice_inference` | 不用 `inference_main.py`（它依赖 cwd 下的 `inference/chunks_temp.json` 与全局 `spk_mix_map`） |
| 导入判定 | worker 里 `torch.load(map_location="cpu")` + 只建模型结构比形状 | 不加载编码器/声码器，2GB 模型 3–8s |
| 上游源码 | `vendor/` 只读；`sys.path.insert(0, vendor)` | 任何补丁写 `PATCHES.md` 并在 `patch/` 里以运行时注入实现，不改 vendor 文件 |

**等价性保障**：推理 API 路径必须与上游 CLI 行为一致——用"同输入同参数对比"脚本（时长/峰值/包络相关性）作为回归测试（M4 门禁）。

## 9. UI 层结构

- **App shell**：左侧 220px 导航（8 项，任务徽标）+ 顶部状态条（GPU/磁盘/运行中任务）+ 主区 + 右下任务浮层。
- **状态所有权**：`jobs` store 是唯一任务快照源（由 `Channel` 增量 patch，不全量重拉）；页面 store 只存草稿与 UI 开关。
- **渲染预算**：列表虚拟化（TanStack Virtual）；日志用固定高度行 + 环形缓冲 10k；波形/频谱用 canvas，不进 React 树。
- **错误展示**：`ipc/errors.ts` 把 Rust 的 `{code, message_zh, cause_zh, next_action}` 直接映射为三段式组件，禁止页面自定义文案。
- **主题**：Tailwind + CSS 变量（`--bg/--fg/--accent/--danger`），深浅色由 `prefers-color-scheme` + 用户覆盖。

## 10. 模式选择（逐项，含否决理由）

| 决策点 | 选择 | 否决的候选与理由 |
| --- | --- | --- |
| 跨进程通信 | **JSON Lines over stdio** | gRPC（端口/鉴权/依赖重）、ZeroMQ（额外原生库） |
| 任务编排 | **显式状态机 + 服务对象**（`JobState::try_transition`） | actor 框架（actix）：单一职责场景下引入信箱/监督树过度设计；typed-state 泛型：类型爆炸 |
| 进程托管 | **Job Object 封装为 `ProcessHost`** | 直接 `Command::kill()`：杀不掉孙进程（train.py 会 spawn DDP 子进程） |
| DB 访问 | **Repository（`JobRepo` trait）+ 手写 SQL** | ORM（diesel/sqlx 宏）：编译期依赖重、迁移不透明；只用 rusqlite 参数化 SQL |
| 事件分发 | **单点 EventHub + 合并** | 每命令各自 emit：前端事件风暴、顺序不可控 |
| 前端状态 | **zustand 切片** | Redux Toolkit：模板代码多；Context：高频更新整树重渲染 |
| 上游调用 | **Adapter（`UpstreamRunner`）+ 参数结构体** | 直接拼 CLI 字符串：参数遗漏/注入、无法单测 |
| 依赖注入 | **构造函数注入 + trait 泛型（测试用 fake）** | DI 容器：运行时解析、错误晚暴露 |

## 11. 测试策略

| 层 | 工具 | 必测内容 |
| --- | --- | --- |
| Rust domain | `cargo test` | 状态迁移合法性、路径规则、兼容性判定（喂 fixture config/state_dict 形状）、错误码映射 |
| Rust services | `cargo test` + fake traits | JobManager 取消/OOM/心跳超时、导入 staging 原子性、对账 |
| 契约 | `cargo test` + `vitest` 读同一份 JSON Schema | 事件/命令字段不漂移（CI 生成 diff 即失败） |
| 前端 | `vitest` + React Testing Library | 关键页面状态（空/阻断/失败）、DropZone 判定分支、错误三段式 |
| Python bridge | `pytest` | 参数装配（每个 CLI 参数一条）、日志解析、事件顺序、退出码映射 |
| 端到端 | Playwright（Tauri WebDriver）+ 脚本化 GPU 任务 | 首启→doctor→导入→冒烟→推理（M1/M4 里程碑各一次） |

TDD 适用：domain/services/bridge。例外：Tauri 配置、安装器脚本、纯样式——用冒烟脚本 + 截图替代，理由记录在 plan。

## 12. 构建与打包

- 前端：`pnpm build` → `dist/`；Tauri `frontendDist`。
- Rust：`cargo build --release`；`tauri build` 产出 NSIS 安装器（主）+ MSI（备）。
- 资源：`resources/runtime/`（CPython + site-packages）、`resources/worker/`、`resources/vendor/`、`resources/assets-manifest/`。
- WebView2：`webview2InstallMode: offlineInstaller`，安装器内置 Evergreen Standalone（约 130MB）。
- 体积预算：安装包 ≤2.2GB（runtime 1.6 + 前端 15 + WebView2 0.13 + 余量）——**比原 7GB 预算大幅下降**，因为不再打 PyInstaller 冗余副本。
- 性能预算（与 [spec §12](../spec/spec.md) 同源，不得放宽）：冷启动可交互 ≤1.5s、路由 ≤100ms、IPC 往返 P95 ≤10ms、训练满载 0 个 >50ms 长任务、内存常驻 ≤250MB、安装包 ≤2.2GB。每一项对应一个 `perf/` 基准脚本，发布前跑全样本并写 `perf/baseline.json`。
- 质量预算：Rust 核心覆盖率 ≥85%、Python 领域 ≥90%、前端关键路径 ≥80%；三个解析器 fuzz ≥10⁶ 次；静态检查零警告；`perf` 回归 >5% 阻断发布。
- 校验：安装后跑 `svcstudio.exe --doctor --json`；DLL 扫描脚本检查 `torch_cuda.dll`/`cudnn*`。
- 合规：`licenses/` + `source/` + `sbom.json`（含 cargo/pnpm/pip 三方）。

## 13. 扩展缝

- **新任务类型**：`JobKind` 枚举 + worker `jobs/<kind>.py` + 前端 `features/<kind>`，三处注册。
- **新编码器**：`domain::Encoder` 枚举 + `assets` 清单条目 + `preprocess` 参数映射，不改调度。
- **新资产类型**：`AssetKind` + 清单 schema，不动 DB。
- **i18n**：`lib/i18n-zh.ts` 单文件字典；首发只 zh，但文案不写死在组件里。

## 14. 架构风险

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R-ARCH-01 | Rust 侧工作量与团队熟练度 | M1 延期 | 先做"能跑空 job"的骨架 + fake 测试；UI 与 Rust 并行开发，用契约 mock |
| R-ARCH-02 | WebView2 在 Win10 干净机缺失 | H1 失败 | 安装器内置离线包；A8 spike |
| R-ARCH-03 | 内置 runtime 与 Tauri resources 路径解析 | 首启失败 | 统一 `paths::resource_dir()`，禁用相对路径；A2 spike |
| R-ARCH-04 | 前端事件风暴导致卡顿 | G1 失败 | 100ms 合并 + 环形缓冲 + 虚拟列表；压测 5k 行/分钟 |
| R-ARCH-05 | 推理 API 与 CLI 行为漂移 | F1 不达标 | 等价性回归脚本进 M4 门禁 |
| R-ARCH-06 | 三语言契约漂移 | 联调返工 | `contracts/` 单一事实源 + CI codegen diff |
