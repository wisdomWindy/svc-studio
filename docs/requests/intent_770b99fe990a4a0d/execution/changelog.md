# 执行记录（changelog）

> 每条记录一次"可验证的增量"：改了什么、用什么命令验证、结果如何。没有证据的条目不写。

## 2026-11-16 · WP0 工程基座

### W0-1 前端工具链与目录骨架

- 新增：`package.json`（React 19 / Vite 6 / TS 5.7 / Tailwind 3 / vitest 2 / Tauri CLI 2）、`tsconfig.json`（strict + `noUncheckedIndexedAccess`）、`vite.config.ts`、`tailwind.config.ts`、`postcss.config.cjs`、`index.html`、`.npmrc`、`.gitignore`、`.editorconfig`。
- 设计 token 唯一来源：`src/styles/index.css`（深/浅两套 CSS 变量，禁止业务代码硬编码色值）。
- 验证：`pnpm install`（见下）/ `pnpm typecheck`。

### W0-2 Tauri 壳

- 新增：`src-tauri/Cargo.toml`、`build.rs`、`tauri.conf.json`（1180×720 最小窗口、CSP、离线 WebView2 安装模式）、`capabilities/default.json`（只开 dialog/notification/window-state + 自定义命令）。
- Rust 侧：`error.rs`（错误对象契约）、`commands/bootstrap.rs`（数据根 `%USERPROFILE%\SVCStudio`、目录就绪、版本握手）、`lib.rs`（插件注册与命令表）。
- 验证：`cargo check` / `cargo clippy -D warnings`。

### W0-3 页面壳

- 新增：`AppShell`（侧栏 + 7 步流水线条 + 数据目录/运行时状态）、`Button`、`PageHeader`、8 个页面骨架（项目/数据集/预处理/训练/推理/模型库/任务/设置）。
- `src/lib/api.ts`：IPC 唯一出口（超时 15s、错误归一为 `AppError`、dev 日志）；浏览器预览下 `bootstrap` 走本地兜底，保证 `pnpm dev` 可用。
- 验证：`pnpm typecheck` + `pnpm dev` 手开。

### W0-4 质量门聚合

- 新增：`scripts/check.ps1`（tsc → vitest → pytest → 体积预算 → cargo fmt/clippy）、`scripts/assert-budget.mjs`（首屏 JS gzip ≤180KB）。
- 验证：`pwsh scripts/check.ps1` 退出码 0。

### W0-5 性能基线

- 新增：`perf/bench.ts`（产物 gzip 体积必须真实；运行态指标留 `null`，由后续 e2e 回填，禁止写假数）。
- 验证：`npm run bench` 生成 `perf/baseline.json`。

### Worker 骨架

- 新增：`worker/pyproject.toml`、`svc_bridge/{__init__,rpc,exit_codes,__main__}.py`、`worker/tests/test_rpc.py`。
- 协议：JSON Lines、单写者加锁、`stdout` 只走协议（第三方 print 必须改道 stderr）。
- 验证：`python -m pytest worker/tests -q`（6 用例）。

### 实现期踩到的两个真问题（已修，记入架构约束）

1. **窗口状态插件会把"最小化"一起恢复**：首次关闭时窗口处于最小化，之后每次启动窗口都以 16×16、无标题、隐藏状态出现（`GetWindowRect` 实测）。判定为不可接受的一类"启动即隐身"缺陷，直接移除 `tauri-plugin-window-state`，改由配置固定 1180×720 居中；记忆窗口尺寸留到 WP8 用带测试的实现替代。
2. **dev 模式 CSP 阻断 Vite HMR 内联脚本**：`devUrl` 下 React Refresh 的前置脚本是内联 `<script>`，被 `script-src 'self'` 拦掉，页面全黑。新增 `app.security.devCsp`（仅 dev 放开 `'unsafe-inline'` 与 `ws://localhost:5173`），生产 CSP 不变。

### 验收证据（M0）

| 命令 | 结果 |
| --- | --- |
| `pnpm install` | 387 包，esbuild 构建脚本放行（pnpm 11 需 `allowBuilds`） |
| `pnpm typecheck` | exit 0（strict + `noUncheckedIndexedAccess`） |
| `pnpm test` | 3 文件 / 5 用例通过 |
| `python -m pytest worker/tests -q` | 6 用例通过 |
| `pnpm build` | 产物 gzip 122.1KB（预算 180KB） |
| `node scripts/assert-budget.mjs` | PASS |
| `cargo clippy --all-targets -- -D warnings` | exit 0（`AppError` 改为 `Box` 传递，修掉 `result_large_err`） |
| `powershell -File scripts/check.ps1` | `RESULT PASS` |
| 启动实测 | 窗口 1196×759（1180×720 + 边框），标题 `SVC Studio`，`visible=True, iconic=False` |
| 截图 | `evidence/W0-3/window.png`（1196×759） |

未验证项（不写成通过）：dev 模式（`tauri dev`）在用户前台游戏运行时无法置前观察，故 dev 交互只做了 CSP 修复与构建验证，未做人眼确认；留待 WP8 首次联调时补。


## 2026-11-16 · WP1 存储与领域内核

### W1-1 / W1-2 schema 与数据库

- `src-tauri/migrations/V001__init.sql`：19 张表（附录 C 的 18 张 + `import_sessions`），全部枚举带 `CHECK`，关键外键带 `ON DELETE CASCADE/SET NULL/RESTRICT`，6 个索引。
- `infra/db.rs`：WAL + `synchronous=NORMAL` + `foreign_keys=ON` + `busy_timeout=5s`；`migrate()` 幂等、升级前备份 `studio.db.bak.<v>`、事务内写 `user_version`。
- 验证：`cargo test` → `migrate_creates_schema_and_is_idempotent`、`foreign_keys_are_enforced`、`status_check_rejects_unknown_state`、`migration_keeps_user_version` 全绿。

### W1-3 领域模型与前端镜像

- `core/models.rs`：Project / Job / JobEvent / ModelCard / DatasetSummary / JobStatusCount，serde camelCase。
- `src/types.ts`：新增 Project / ModelCard / DatasetSummary / JobStatusCount / CreateProjectInput，注释标明镜像来源；Bootstrap 增加 `reconciledJobs`。
- 验证：`pnpm typecheck` → 0。

### W1-4 仓储

- `core/repo/{projects,jobs,models,datasets,settings}.rs`：全部参数化 SQL；`set_status` 只允许状态机认可的迁移；`models::insert` 对同 sha256 返回 `SVC-2002`。
- 验证：`cargo test` 覆盖 CRUD、重复 slug、重复 sha、状态迁移非法、事件 seq 自增。

### W1-5 文件系统原语

- `infra/fs.rs`：`atomic_write`（同目录 tmp → sync → rename）、`safe_join`（拒绝绝对路径/盘符/UNC/`..`/NUL + 词法归一后前缀校验）、`sha256_file`/`sha256_bytes`。
- 验证：路径穿越 7 例、临时文件不残留、sha256 已知向量一致。

### W1-6 启动对账

- `jobs::reconcile`：`running|stopping` 且 pid 不存在 → `interrupted`。
- `models::mark_missing`：磁盘文件不存在 → `missing`（保留记录，库列表隐藏）。
- 验证：`reconcile_marks_orphans_interrupted`、`mark_missing_hides_deleted_files`。
- 实机：启动日志 `INFO state ready db=C:\Users\yangjianlin\SVCStudio\studio.db version=1 reconciled=0`，`studio.db` 已生成。

### 命令面（WP1 暴露给前端）

`create_project / list_projects / get_project / delete_project`、`list_jobs / get_job / job_events / jobs_summary / cancel_job`、`list_models / get_model / default_model / set_default_model / delete_model`、`list_datasets`、`get_settings / set_setting`、`bootstrap / ping`。

### 质量门

- `cargo test`：28 passed
- `cargo clippy --all-targets -D warnings`：0
- `pnpm typecheck` / `pnpm test` / `pytest` / 体积预算：全绿（见 `scripts/check.ps1`）

### 已知限制（诚实记录）

- `core` 与 `infra/fs` 暂时 `#![allow(dead_code)]`：WP1 先建齐 API，WP2+ 接线后删除（task-board 已记 W7-5 复查）。
- 窗口在游戏全屏时会被系统最小化（`rect=-31993,-32000`），非应用缺陷；UI 验证仍以 M0 截图 + WP8 e2e 为准。

## 2026-11-16 · WP2 进程宿主与协议

### 协议（W2-1）

- `bridge/protocol.rs`：`WorkerEvent`（hello/stage/progress/log/result/error/cancelled/stopped/pong）用 `#[serde(tag="kind")]`；未知字段容忍、未知 kind 拒绝；控制指令固定单行 JSON。
- 验证：5 个解析测试（带/不带 stage、error 可选字段、未知字段容忍、非 JSON 拒绝、控制指令单行）。

### 宿主（W2-2 / W2-6）

- `bridge/host.rs`：`SpawnSpec` → spawn；stdout 逐行解析（非协议行降级为 `warn` 日志并截断 2000 字符）；stderr 单独线程走 `debug`；有界通道 1024 形成背压；`stop(grace)` / `cancel(grace)` 超时即杀树。
- 验证（真实 Python 进程，fixture `worker/tests/fixtures/fake_worker.py`）：
  - 正常：hello + 3 条 progress + result + exit 0
  - 崩溃：exit code 3 原样带回
  - 协作停止：发 `stop` 后 exit 0 且 `killed=false`
  - 挂起：300ms 宽限后 `killed=true`
  - 日志洪水：2000 条不丢不卡死
  - 孙进程：`tree` 场景下 `tasklist` 轮询确认孙进程被 Job Object 收掉

### Job Object（W2-3）

- `infra/job_object.rs`：`CreateJobObjectW` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `AssignProcessToJobObject`；`RunningJob::drop` 兜底 terminate，避免漏杀。
- 验证：Assign/Terminate 真实进程 + 孙进程回收测试通过。

### 调度（W2-4）

- `core/scheduler.rs`：GPU 单并发信号量（`acquire` 排队、`try_acquire` 探测、`in_use` 状态）+ `Priority` 枚举。
- 验证：并发测试证明第二个持有者必须等待，释放后立即获得。
- 说明：优先级**排序**随 WP6 的任务队列一起落地；当前是单并发 FIFO，这与 spec §8 的"同一时刻一个吃显存作业"一致。

### 集成（W2-5）

- `core/runner.rs`：运行中作业注册表（插入/取出/存在性）。
- `commands/runner.rs`：`run_diagnostics` 真实拉起 `python -m svc_bridge rpc`，把事件写 `job_events`、推进 `jobs.progress`、用 `job:event` / `job:done` 广播；`stop_running` 统一停止/取消。
- `cancel_job` 升级为 async：运行中任务先终止进程树再落 `cancelled`；句柄不在本进程时按 `interrupted` 处理。
- 验证：`worker_events_are_persisted_and_progress_moves`（进度 0.5、3 条事件、错误码落库）。

### 质量门

- `cargo test`：44 passed（含 6 个真实进程场景）
- `cargo clippy -D warnings`：0

## 2026-11-16 · WP3 环境与资产

### vendor 上游（W3-1）

- `scripts/vendor-upstream.ps1` 的等价操作：`git clone --depth 1 -b 4.1-Stable` 到 `vendor/so-vits-svc`，`vendor/UPSTREAM.json` 记录 commit `730930d337d171479eadf305f96cbed4bb393e77` 与抓取时间。
- 该目录在 `.gitignore` 中（发布时由脚本重新拉取并随源码归档）。

### doctor 九项（W3-3）

- `worker/svc_bridge/commands/doctor.py`：python/torch/cuda/gpu/ffmpeg/disk/encoder/f0/vocoder；每项 `pass|warn|fail` + `cause` + `fix` + `blocking`；任一项异常都不抛出。
- 输出走 `sys.stdout.buffer` 写 UTF-8（Windows 控制台 cp936 会把中文编坏——这是实测踩到的坑）。
- 验证：`pytest` 7 个用例（结构、缺权重带修复动作、ffmpeg 两种状态、磁盘低于阈值、CLI 单对象 UTF-8）。
- 端到端：Rust `run_doctor` 真实拉起 Python，`runs_real_worker_and_parses_nine_items` 断言 9 项、`trainable` 与阻断项一致、失败项都有 fix。

### 资产清单与验签（W3-4）

- `core/assets/manifest.rs`：`deny_unknown_fields` + schema 校验；Ed25519 验签（公钥编译期嵌入 `assets/trust/pubkey.hex`，当前为空=开发未签名态，`SVCSTUDIO_ALLOW_UNSIGNED_MANIFEST=1` 显式放行，发布必须签名）；`verify_file` 同时比大小与 sha256。
- 验证：schema 漂移、未知字段、签名往返、篡改不过、大小/哈希/缺失三态。

### 离线导入（W3-5 的导入部分）

- `core/assets/importer.rs`：按 manifest 逐个文件处理；支持嵌套路径与平铺目录两种来源；目标已存在且校验通过则跳过（幂等）；先写临时文件、校验后再改名；源文件只读。
- 验证：嵌套导入 + 二次幂等、平铺查找、哈希不符不落盘、缺文件只报告不中断。
- 下载/镜像/断点续传未实现：需要真实下载源与网络用例，随 WP9 资产落地一起做。

### 诊断包（W3-6）

- `core/diagnostics.rs`：白名单（doctor/versions/config-snapshot/manifest-check/logs/tail.txt + extras 下 txt/json/log）；`audit_package` 二次扫描，发现非白名单条目或 RIFF 头即失败。
- `commands/diagnostics.rs`：跑 doctor、汇总版本与配置、取最新日志尾部 2000 行，打包到 `<dataRoot>/diagnostics/`，打包后立即自检。
- 验证：白名单打包 + 跳过 extras/wav、UTF-8 截断、伪装成文本的 WAV 被 audit 拒绝、白名单精确匹配。

### 命令面新增

`assets_manifest`、`import_asset_dir`、`doctor_report`、`export_diagnostics`。

### 质量门

- `cargo test`：63 passed；`clippy -D warnings`：0；`pytest`：13 passed（含 doctor）；`check.ps1`：PASS

## 2026-11-16 · WP4 数据集与 QC

### 音频原语（纯标准库）

- `worker/svc_bridge/audio.py`：16-bit PCM 读写、10ms 帧 RMS、静音比、削波比（连续 ≥3 样本达 99.9% 满刻度）、峰值/RMS dBFS、去首尾静音、线性重采样、峰值保护（默认 -1dBFS）。
- 不依赖 numpy/librosa：QC 与切片必须在"只有内置运行时"的机器上可跑，也让测试能用合成波形精确断言。

### 结构校验（W4-1）

- `dataset.scan`：说话人目录、空目录、多层嵌套、非 wav（给转换建议）、0 字节、无法解码；问题分类为 `blocking`。
- 验证：结构问题用例、缺目录用例。

### QC 七项（W4-2 / W4-5）

- 指标：总时长、时长 min/p50/p95/max、采样率分布、声道分布、静音比、削波比、响度（`method=rms`，mean/p10/p90）；阈值断言产出 `blocking`/`warnings`。
- 阻断规则：单条 >30s、静音比 >20%、削波 ≥0.1%、采样率非 44100、非单声道；低数据量只告警。
- 验证：时长/采样率/声道一致性、静音 50% 触发阻断、方波触发削波阻断、31s 触发阻断、干净集通过、CLI 非零退出码。

### 切片（W4-3）

- 5–15s，静音点优先切、找不到就硬切；原文件移入 `quarantine/<spk>/`；重复执行不再切（幂等）。
- 验证：22s 文件切出 ≥2 段且每段 4.9–15.1s、原文件消失、quarantine 存在、二次运行 `outputs == {}`。

### 重采样与峰值（W4-4）

- 8k 立体声 → 44.1k 单声道；去首尾静音；峰值压到 -1dBFS。
- 验证：转换后采样率/声道正确、时长误差 <50ms、峰值 ≤ -0.9dBFS。

### 指纹与落库（W4-6）

- `dataset_qc` 命令：跑 QC → 写 `reports/qc_report.json`（原子写）→ 落 `datasets`（新版本）、`dataset_files`（含 sha256）、`qc_reports`；指纹 = 排序后的 `relPath:seconds:sr:channels` 的 sha256（与时序无关）。
- 验证：报告反序列化、指纹顺序无关且对时长敏感、空集稳定。
- 跨语言：`scan_runs_against_real_worker` 真实拉起 Python 扫描手写 WAV，断言 1 个说话人 / 1 个文件 / 时长 1.0s。

### 口径偏差（写进 PRD）

- 响度从 LUFS 改为 **RMS dBFS**（`method=rms`）：不引入 pyloudnorm，口径写进报告；PRD §5.4 与 C2 说明同步修改。

### 质量门

- `cargo test`：66 passed；`pytest`：24 passed；`clippy -D warnings`：0；`check.ps1`：PASS

## 2026-11-16 · WP5 预处理与配置

### 三步编排（W5-1 / W5-5）

- `worker/svc_bridge/commands/preprocess.py`：`build_steps` 产出三条命令（cwd=项目根、`--skip_loudnorm`、`--vol_aug`、`--use_diff`、`--num_processes`）；`run_chain` 逐步执行、失败即停、写 `reports/preprocess_manifest.json`（原子写）。
- 幂等：上游脚本按文件存在性跳过；我们只统计与校验，重跑不会重复产物。
- 验证：命令参数逐项断言、三步成功、失败停在第 2 步并记录 exitCode、重跑 counts 不变且 diffs 为空。

### 计数一致性（W5-2）

- `count_products` 统计 `.wav/.soft.pt/.f0.npy/.spec.pt/.vol.npy/.mel.npy/.aug_mel.npy/.aug_vol.npy`；`diff_products` 以 wav 数为基准，`--use_diff` 或 `vol_embedding` 时追加期望。
- 验证：缺失产物进 diff、`use_diff` 期望 4 类附加产物、config 的 `vol_embedding=true` 时期望 `.vol.npy`。
- Rust：`preprocess_start` 把 job 与 `preprocess_runs` 落库，事件里写 `counts`；`preprocess_manifest` 读取 JSON。

### 配置校验与断言（W5-3 / W5-4）

- `commands/config.py`：config/diffusion 类型与范围校验（batch 1–32、lr、keep_ckpts、sr=44100、ssl_dim 与编码器匹配、声码器白名单）；10 条断言（duration < 最短切片、k_step_max < timesteps、spk 非空、whisper 25s 上限等）。
- 验证：合法对通过、缺字段带路径、越界、ssl_dim 不匹配、k_step_max、duration ≥ 最短切片、whisper 25s 上限、空 spk、输入不被修改、CLI 退出码 0/2。
- 跨语言：`config_assert_blocks_duration_longer_than_shortest_clip` 真实拉起 Python 校验，断言 `SVC-4002`。

### 质量门

- `cargo test`：67 passed；`pytest`：40 passed；`clippy -D warnings`：0；`check.ps1`：PASS

## 2026-11-16 · WP6 训练

### 日志解析与推荐（W6-1 / W6-4）

- `core/train/logparse.rs`：解析 `Losses: [...], step: N, lr: X`（含 loguru 前缀、`tensor(...)` 记法）、`Train Epoch`、`G_N.pth`；OOM 与 NaN/Inf 单独成信号。
- 修掉一个真实误判：整行小写后 `INFO` 里含 `inf`，会把正常日志判成发散；现在只在损失列表内部检查 nan/inf。
- `core/train/recommend.rs`：6/8/12/24GB 四档 + 6GB 下限；低于下限 `trainable=false`。
- 验证：8 个解析用例、4 个推荐用例（单调性、6GB 表值、下限、reason 必含 OOM 提示）。

### 冒烟（W6-2）

- `worker/svc_bridge/commands/train.py`：复制配置覆盖 `eval_interval=50 / log_interval=10 / keep_ckpts=2`，`-m _smoke` 写独立目录；读到第 100 步终止整棵进程树（Windows `taskkill /T /F`，因为 train.py 会派生 DataLoader 进程）。
- 验证：`test_smoke_stops_at_max_steps_and_emits_progress`（fake train 130 步，跑到 100 停、进度事件到最后一步）；`build_smoke_config` 不改原文件。

### 长训与自愈（W6-4 / W6-5 / W6-6）

- `commands/train.rs`：host 事件泵→jobs 进度；`RunningJobs` oneshot 取消；OOM 只重试一次，`batch_size` 减半写回配置并记录 `SVC-5001`；NaN 直接失败不重试。
- 端口：`config.train.port` 被占用时自动挑空闲端口并写回（含两个端口测试）。
- 新增/续训判定：`logs/44k` 有未登记 checkpoint 时拒绝静默续训，要求显式确认或先归档。
- 验证：OOM 检测（fake 脚本）、NaN 检测、端口避让与保留、checkpoint 列表。

### 可选能力与登记（W6-7 / W6-8）

- `worker/svc_bridge/commands/aux.py`：聚类 / 特征检索索引；脚本成功但产物缺失判失败。
- `commands/train.rs::train_aux`：30 分钟上限，返回产物路径与输出尾部。
- `commands/models.rs::register_model`：校验路径在项目内、`G_N.pth` 可解析步数、config 存在，落库并（无默认模型时）设为默认。
- 验证：aux 五用例（产物、缺产物、崩溃、CLI）、`step_from_checkpoint` 五例。

### 质量门

- `cargo test`：81 passed；`pytest`：53 passed；`clippy -D warnings`：0；`check.ps1`：PASS

## 2026-11-16 · WP7（上）：兼容判定与上传导入

### 7 步兼容判定（W7-1）

- `worker/svc_bridge/commands/model.py`：三种封装归一（裸 state_dict / `{"model":...}` / `module.` 前缀）、必需前缀（`enc_p/flow/dec/emb_g/ref_enc`）、`ssl_dim` 与权重形状比对、缺 `enc_q.*` 判 `compressed`、`emb_g` 行数与 `spk` 数量不一致给提示。
- 没有 torch 时返回 `verdict=unknown` + `SVC-1001`，不假装判定。
- 验证：11 个用例（封装、必需前缀、压缩、ssl_dim、缺 model 段、说话人数、推断编码器、whisper 25s、无 torch、不改入参）。

### 上传六步（W7-2）

- `core/model/import.rs`：`plan_upload`（只认 1 权重 + 1 config，其它忽略并记录）、`slug_for`（文件系统安全）、`stage_copy`（复制 + 复算 sha256 + 源文件不动）、`finalize`（原子移入 `imports/<slug>/model/`）。
- `commands/inference.rs::import_model_upload`：信任勾选（未勾选 `SVC-6009`）→ 重复 sha 拒绝（`SVC-2002`）→ staging → 判定 → 不可识别进 `_unrecognized/` 并报 `SVC-6005`/`SVC-1001` → 通过则落库、压缩模型标 `compressed`、无默认时设默认。
- 验证：配对（1 个/2 个/缺一半）、复制校验、落位、slug、文件名解析；跨语言 `model_check_reports_missing_torch_readably` 真实拉起 Python 断言 `unknown/incompatible`。

### 推理参数规则（W7-3 的前置）

- `worker/svc_bridge/commands/params.py`：默认值、whisper 强制 `clip=25/lg=1`、歌声禁自动音高、浅扩散禁增强、特征检索与聚类互斥、比例 0 清空路径、纯扩散需模型、范围校验。
- 验证：11 个用例，含"入参不被修改"。

### 质量门

- `cargo test`：88 passed；`pytest`：75 passed；`clippy -D warnings`：0；`check.ps1`：PASS

## 2026-11-17 · WP7（下）：推理批次、批量隔离、压缩/ONNX/混合规则

### 推理适配器与批次（W7-3 / W7-4）

- `worker/svc_bridge/commands/infer.py`：`to_svc_kwargs` 严格对齐 `Svc.slice_inference` 参数名；`normalize_input` 统一 44.1k 单声道 -1dBFS；`prepare_batch` 建立 `outputs/<时间戳>_<批次>/{inputs,outputs}`；`run_batch` 逐条执行、逐条统计 rtf/峰值/削波/时长并原子写 `report.json`。
- 真实执行在 `_real_runner_factory`（延迟导入上游 `Svc`），测试注入 runner，因此批次逻辑不依赖 torch。

### 批量隔离（W7-5）

- 坏文件在预处理阶段就进失败清单（不拖垮整批），主循环跳过它；批次状态 `succeeded / succeeded_with_errors / failed` 三态。
- `retry_failed` 只重跑可重跑项：导入阶段就失败的条目没有输出路径，明确标注"需要重新提供文件"，不假装能重试。
- `commands/inference.rs::infer_start`：参数写 JSON 文件（避免命令行转义/长度问题）→ host 事件泵 → 落 `inference_runs`（ok/failed/params_hash）；`infer_batches` 从 `report.json` 汇总批次列表。
- 验证：参数映射、输入规范化、报告与目录、坏文件隔离、重试语义、paramsHash 稳定。

### 压缩 / ONNX / 混合规则（W7-6 / W7-7 的 worker 侧）

- `commands/modeltool.py`：压缩调用上游 `compress_model.py` 并校验产物；ONNX 导出**复制脚本再改 `path`**，绝不改 vendor 源码；失败标注"实验性"。
- `commands/mix.py`：2–8 个模型、仅单说话人、结构一致、权重为正；凸组合归一、线性保留原值。
- 验证：压缩成功/无产物、ONNX 补丁不改源文件、无 path 时报错、混合九条规则。
- 未完成：压缩/混合的 Tauri 命令层（worker 已可用，下一轮接入）。

### 质量门

- `cargo test`：88 passed；`pytest`：91 passed；`clippy -D warnings`：0；`check.ps1`：PASS

## 2026-11-17 · WP8（上）：8 页接通真实命令

### 数据层（W8-1 / W8-2）

- `src/lib/backend.ts`：全部 Tauri 命令的类型化封装（项目/数据集/预处理/训练/模型/推理/任务/设置/资产/诊断），参数命名与 Rust 侧 camelCase 约定一致。
- `src/lib/store.ts`：zustand 全局状态（项目、当前项目、模型、任务），`setProjects` 保留当前选择、失效时回退。
- `src/lib/events.ts` + 轮询兜底：任务页 2s 拉 `job_events`，不依赖事件通道是否可用。
- `src/lib/api.ts`：`call` 在浏览器预览下明确抛"无后端"，避免静默失败；改为静态导入 `invoke`，消掉打包时的动态导入告警。

### 页面（W8-4 第一部分）

| 页面 | 已接能力 |
| --- | --- |
| 项目 | 列表、创建（编码器/F0/素材类型）、选择、删除（二次确认）、数据目录展示 |
| 数据集 | 结构扫描、QC 七项卡片、阻断/告警清单、切片、重采样 |
| 预处理 | 启动三步链路、产物计数与差异、失败步骤、配置断言 |
| 训练 | 显存推荐、100 步冒烟、长训、停止、checkpoint 列表、一键登记模型 |
| 推理 | 模型选择、音频多选、变调/F0/切片参数、提交批次、历史批次摘要 |
| 模型库 | 上传（仅 1 模型 + 1 配置）、来源可信勾选、设为默认、删除 |
| 任务 | 统一任务表、进度、取消、事件日志面板 |
| 设置 | 环境自检九项、诊断包导出、主题切换、版本与数据目录 |

### 证据

- `pnpm build`：JS gzip **131.8KB / 上限 180KB**（`scripts/assert-budget.mjs` PASS）。
- `pnpm test`：18 passed（新增 format / store / backend 命令契约三组用例）。
- 实机窗口：`evidence/WP8/projects.png`（PrintWindow 截取，窗口被前台全屏应用遮挡时仍可取证）。
- 说明：本轮窗口启动后处于最小化（`-32000`），需 `ShowWindow(SW_RESTORE)` 才能显示；这是环境前台全屏应用导致，已记录，WP8 收尾时补"启动即前台"的处理。

### 未完成

- 虚拟列表、波形/A/B 播放器、错误三段式组件、无障碍走查、运行态性能探针（W8-3/5/6/7）。

## 2026-11-17 · WP8（下）与 WP9 脚本

### 错误三段式（W8-5）

- `components/ErrorNotice.tsx`：现象（消息）+ 原因 + 下一步（动作按钮与 deeplink），带严重级与错误码；纯字符串错误不会伪造原因。
- `useAsyncAction` 增加 `appError`（结构化），8 个页面统一用它渲染，不再出现裸英文异常。
- 验证：4 个组件用例（三段齐全、纯字符串、空值、点击重试）。

### 波形与 A/B（W8-3 部分）

- `components/ui/Waveform.tsx`：延迟加载 wavesurfer，无音频时显示占位而不是空白。
- `components/AudioPair.tsx`：原声/输出双波形；本地路径经 `convertFileSrc` 走 asset 协议；浏览器预览明确提示不加载本地音频。
- 推理页：点击历史批次 → 读 `infer_batch_report` → 选择条目 → 显示 A/B。
- Tauri 配置：开启 `assetProtocol.scope`（仅 `$HOME/SVCStudio/**` 与 `$TEMP/**`），并启用 `protocol-asset` feature。
- 验证：2 个占位用例 + 类型检查；真实音频波形需桌面端有批次产物（本机无模型，待集成）。

### 打包脚本（WP9 脚本层）

- `scripts/build-runtime.ps1`：Python 3.8.9 embeddable → pip → `worker/requirements.lock.txt` → 复制 worker/upstream → `import torch, fairseq` 自检；`-DryRun` 只打印计划。
- `scripts/stage-runtime.ps1`：复制 runtime/vendor/assets 到 `src-tauri/resources`，生成 `MANIFEST.json`（相对路径 + sha256），并打印体积。
- `scripts/verify-dlls.ps1`：检查 `c10.dll / torch_cpu.dll / torch_cuda.dll / cudnn64_8.dll / nvrtc64_*.dll`，并逐文件比对安装目录哈希。
- `scripts/release.ps1`：构建 → SHA256SUMS → SBOM（CycloneDX 1.5）→ licenses（AGPL 全文 + SOURCE-OFFER）→ `source-<ver>.zip`。
- `worker/requirements.lock.txt`：锁定 torch 1.13.1+cu117 / torchaudio 0.13.1 / fairseq 0.12.2 等基线组合。

### 修复的真实问题

- PowerShell 5.1 按 ANSI 读取无 BOM 的 UTF-8 脚本，中文注释直接导致语法错误；全部 `.ps1` 改为 **UTF-8 with BOM** 后可正常执行。
- `tauri build` 因应用仍在运行而 `os error 5`（exe 被占用）：发布脚本与本地验证需先结束进程。

### 证据

- `build-runtime.ps1 -DryRun` / `stage-runtime.ps1 -DryRun` / `release.ps1 -DryRun` / `verify-dlls.ps1 -DryRun` 全部 exit 0，并如实报告"缺少 runtime/"。
- `check.ps1`：PASS（tsc / vitest 24 / pytest / 体积 / fmt / clippy / cargo test 88）。

### 未完成（诚实）

- 真实安装包尚未产出：需要下载并安装 ~2GB 的 torch/fairseq 运行时，再在干净 VM 验证；本机只有网络与 D 盘空间，缺少 GPU 与模型权重。
- A/B 的同步播放、波形缩放；无障碍走查；运行态性能探针。

## 2026-11-17 · 首次启动门禁（A2）、A/B 同步播放、验收台账

### A2 使用规约确认（P0）

- 后端：`consents_status` / `accept_consents`（写 `consents` 表，记录 `doc_version` + `app_version` + 时间 + 哈希；已确认不重复写）。
- 前端：`ConsentGate` 覆盖层，四项勾选（AGPL / 使用规约 / 数据集授权 / 真人声音告知），未全部勾选时"确认并继续"禁用。
- 验证：3 个组件用例（四项渲染且按钮禁用、已完成时隐藏、勾选后调用并回调）。

### A/B 同步播放（W8-3 增量）

- `Waveform` 暴露 `play/pause/getDuration` 控制器；`AudioPair` 同时起播/暂停，按钮带 `aria-label`。
- 验证：2 个用例（占位 + 按钮禁用、浏览器预览提示）。

### 验收与评审台账（S10 前置）

- 新增 `verification/verification.md`：49 项逐条状态（✅ 26 / 🟡 15 / ⛔ 8），并列出 6 条复现命令。
- 新增 `review/review.md`：代码评审清单 + 8 条已知识缺陷 + 6 条放行条件，结论明确写"未通过"。

### 外部阻塞（有测量数据）

- 目标运行时需下载 torch 1.13.1+cu117 等约 **2.2GB**；本机实测 Python 8MB 安装包 20 分钟只下到 **2.61MB（约 2.2KB/s）**，按此速率 2.2GB 需约 11 天。因此 H1/H2/H4 在本会话不可完成，属外部阻塞而非实现缺陷。
- `tauri build`（release + NSIS）仍在后台编译；即使产出，也只是无运行时的"开发安装包"，不满足 H1。

### 质量门

- `cargo test` 88 / `pytest` 91 / `vitest` 27 / `check.ps1` PASS（含修掉一个 TIME_WAIT 导致的不稳定端口测试）。

## 2026-11-17 · 去掉死代码豁免、GPU 单并发、可重试任务

### 死代码不再被豁免

- 删除 `core/mod.rs` 与 `infra/fs.rs` 的 `#![allow(dead_code)]`，逐项处理：
  - 用起来：`JobStatus`（`set_status` 判终态、`retry_job` 判可重试、事件文案）、`is_complete`（资产导入完成才记 info，否则 warn 并带问题数）、`is_weight`（上传分类）、`safe_join`（批次报告路径校验）、`logparse::parse_line`（训练日志实时识别 OOM/NaN 并发事件）。
  - 删掉真无用的：`fingerprint`、`list_all`、`touch`、`worker_paths`、`Priority`、`is_diverged`。
- 结果：`clippy -D warnings` 0 警告，`cargo test` 87 passed。

### GPU 单并发真正生效

- `Scheduler` 接入 `train_start` 与 `preprocess_start`：任务在 spawn 后先 `acquire()` 再起进程，同一时刻只有一个吃显存的作业。
- 新增 `gpu_busy` 命令（仅提示，不参与门禁）。

### 任务重试

- 新增 `retry_job`：只允许 `failed/interrupted/cancelled/stopped` 重新排队，其它状态返回 `SVC-7002`；任务页对可重试状态显示"重试"按钮。

### 签名状态语义修正

- `verify_manifest` 对"签名存在但不匹配"返回 `SignatureState::Invalid`（此前直接抛错），`enforce` 统一拒绝并给出可操作说明；`Unsigned` 仍受 `SVCSTUDIO_ALLOW_UNSIGNED_MANIFEST` 约束。

### 打包实证（重要）

- `pnpm tauri build` 真实产出 NSIS 安装包：`SVC Studio_0.1.0_x64-setup.exe`，**211.55 MB**，sha256 `63d8a80e…95e5ca`，已复制到 `evidence/H5/`。
- 该包**不含内置 Python 运行时**（runtime 未构建），因此不能作为 H1 证据；但它证明 NSIS + WebView2 离线安装器的构建链路可用。
- **预算风险**：本包 211MB 主要来自 WebView2 离线安装器；再叠加 torch cu117（约 2.2GB）将超过 plan 的 2.2GB 安装体积预算。需要决策：CPU 版 torch（约 200MB）+ CUDA 按需安装，或上调预算。已记入 `review/review.md`。

### 质量门

- `check.ps1` PASS：tsc / vitest 27 / pytest 91 / 体积 133KB / fmt / clippy 0 / cargo test 87。

## 2026-11-17 · 托盘与关闭不误杀（G4）、12 类错误用例（G2）

### G4 关闭窗口 = 收进托盘

- `lib.rs` 新增 `setup_tray`（菜单：显示主窗口 / 退出）与 `setup_close_to_tray`（拦截 `CloseRequested` → `prevent_close` + `hide`）。
- Cargo 开启 `tray-icon` feature；退出只走托盘菜单的"退出"（`app.exit(0)`）。
- **实测**：启动后向主窗口发 `WM_CLOSE` → `process_alive=True`、`visible=False`，任务不中断。

### G2 12 类错误用例脚本

- `scripts/error-cases.ps1`：能本机触发的真跑，不能构造的显式 SKIP。
- 结果：**PASS=9 / FAIL=0 / SKIP=3**，证据 `evidence/G2/error-cases.json`。
  - PASS：缺权重、版本错配、显存不足、配置非法、路径越界、进程崩溃、音频损坏、模型不匹配、端口占用。
  - SKIP（写明原因）：ffmpeg 缺失（本机已装）、磁盘满（需配额）、权限不足（需 ACL）。

### 训练页 GPU 忙闲

- 训练页每 3s 查 `gpu_busy`，标题旁显示"GPU 忙/闲"；只做提示，不阻止排队。

### 质量门

- `check.ps1` PASS：tsc / vitest 27 / pytest 91 / 体积 / fmt / clippy 0 / cargo test 87。

## 2026-11-17 · A2 界面实证（未勾选拦截 / 勾选放行 / 落库）

- 清空 `consents` 表 → 启动应用 → 截到门禁：四项可勾选，"确认并继续"在未全选时不可用。
- 逐项勾选后按钮转为可用（`evidence/A2/consent-checked.png`）。
- 落库验证：`select kind,doc_version,app_version from consents` → 四项齐全，`doc_version=v1`、`app_version=0.1.0`。
- 再次启动：门禁不再出现，直接进入项目页（`evidence/WP8/projects-after-consent.png`）。
- 说明：像素级自动点击在按钮上出现了坐标偏移（点到了上方复选框），因此"点击确认按钮"这一步由落库结果与应用重启后的行为共同证明，而不是单靠一次点击截图。这是取证方式的问题，不是功能缺陷。

## 2026-11-17 · 续训确认（评审缺陷 #4）、错误码落地对照

### 续训不再是死路

- `train_start` 新增 `resume` 参数：已有 `logs/44k` checkpoint 时，未声明续训返回 `SVC-5003`（可操作文案：点「继续训练」或归档 logs/44k）；声明后可正常启动，train.py 自动从最新 checkpoint 续。
- 提取纯函数 `should_block_start(smoke, resume, checkpoints)` + 4 条断言（长训拦截、续训放行、冒烟放行、无 checkpoint 放行）。
- 训练页：有 checkpoint 时按钮文案变为「继续训练」，点击即以 `resume=true` 启动。
- 契约测试同步：`train_start` 参数断言补上 `resume`。

### 错误码落地对照

- 新增 `docs/requests/intent_770b99fe990a4a0d/error_codes.md`：12 条必测项逐条标出"位置 / 自动动作是否真的执行 / 用例状态"，3 条 SKIP 写明构造条件与做法。
- 同时记录一处诚实的"部分"：doctor 的阻断项尚未强制拦住训练入口（当前只做提示），已列为待接线项。

### 质量门

- `check.ps1` PASS：tsc / vitest 27 / pytest 91 / 体积 / fmt / clippy 0 / cargo test 88。

## 2026-11-17 · 环境门禁真正阻断（B1/B2）、训练页提示

### 后端门禁

- `core/gate.rs`：`DoctorGate { trainable, blocking }` + `require_trainable` / `require_from`。
  - 未跑过自检 → `SVC-5001`「还没有做环境自检」
  - 自检有阻断项 → `SVC-5001`「环境自检未通过」+ 逐项列出（cuda、encoder…）
  - 通过 → 放行
- `doctor_report` 跑完即缓存结论到 `AppPaths.doctor`；新增 `doctor_cached` 供前端读取。
- `train_start` 与 `preprocess_start` 第一行即校验门禁，**不再允许带病启动**。
- 验证：4 条断言（未自检拒绝 / 阻断项拒绝并列名 / 通过放行 / 共享槽位读写）。

### 前端

- 训练页读 `doctor_cached`：`trainable=false` 时冒烟与长训按钮禁用，并显示黄色横幅列出阻断项与去处（设置页）。

### 质量门

- `check.ps1` PASS：tsc / vitest 27 / pytest 91 / 体积 / fmt / clippy 0 / **cargo test 92**。

## 2026-11-17 · 预处理页门禁提示、G4 托盘行为取证

- 预处理页读 `doctor_cached`：`trainable=false` 时"开始预处理"禁用并显示阻断横幅（与训练页一致）。
- G4 实测（再次复现）：主窗口可见 → 发 `WM_CLOSE` → 2s 后 `alive=True`、`visible=False`，桌面截图里应用窗口已消失（`evidence/G4/after-close.png`）。
- 托盘图标区域截图（`evidence/G4/tray-area.png`）未能清晰分辨本应用图标：可能落在系统托盘溢出区。**结论只按"进程存活 + 窗口隐藏"认定**，不拿看不清的图标当证据。
- 质量门：`check.ps1` PASS（cargo test 92 / pytest 91 / vitest 27 / clippy 0 / 体积）。

## 2026-11-17 · QC 门禁进预处理（C5 的"阻断"落地）

- 后端：`preprocess_start` 增加 QC 校验——没有报告 `SVC-3003`「数据集还没有质检报告」，报告未通过 `SVC-3003`「数据集质检未通过（N 个阻断项）」；通过才继续。
- 新增 `dataset_qc_verdict` 命令（`exists / passed / blocking`），供前端判断而不必重跑 QC。
- 前端：预处理页读该结论，未跑 QC 或未通过时禁用"开始预处理"，并显示黄色横幅说明先去数据集页。
- 效果：C5 从"QC 报告里有断言"升级为"过不了 QC 就进不了预处理"，前后端一致。
- 质量门：`check.ps1` PASS（cargo test 92 / pytest 91 / vitest 27 / clippy 0 / 体积）。

## 2026-11-17 · D1 预处理链路实测（fake upstream，真实 worker 路径）

- 工具：`tools/d1_preprocess_evidence.py`。它构造 `dataset_raw/spk/{a,b}.wav` 与三个"行为等价"的上游脚本，然后**真实执行** `python -m svc_bridge preprocess`。
- 执行结果（`evidence/D1/d1-preprocess.json`）：
  - exitCode 0
  - 产物 wav/soft/f0/spec 各 2 个
  - `configs/config.json` 已生成
  - manifest `passed=true`，`diffs=[]`
- 这证明的是：CLI 参数解析、三步独立子进程 + cwd=项目根、产物计数、差异清单、manifest 落盘这条链路真的能跑，而不是只有单测。
- 仍不能证明的：真实 `resample.py` / `preprocess_hubert_f0.py` 在 torch + 权重下的行为（需要运行时与权重，属 E2/H2 的同一阻塞）。
- 质量门：`check.ps1` PASS（92 / 91 / 27 / clippy 0 / 体积）。

## 2026-11-17 · 两个会让训练在真机上直接失败的缺陷（已修）

### 1) CLI 子命令缺分发（严重）

- 现象：`python -m svc_bridge train ...` 直接返回 **2**，无任何输出。原因是 `__main__.py` 只注册了 `train/aux/model/infer/modeltool` 的 argparse，**没有写对应分发分支**，落到末尾 `return exit_codes.USAGE`。
- 影响：Rust 侧正是用这些子命令拉起 worker 的，`train_start` / `train_aux` / `model_check` / `infer_start` / `modeltool` 在真实运行时都会"静默失败"。
- 修复：补全五个分发分支，参数逐项透传。
- 防回归：新增 `worker/tests/test_cli_dispatch.py`（5 条），用替身模块断言每个子命令真的被转发、参数不丢。

### 2) 冒烟到 100 步被判失败（严重）

- 现象：冒烟到达 `--smoke-steps` 后由我们主动终止子进程，但 Windows 下 `terminate()` 给出的是**正数**退出码；原逻辑只归一化负数，于是冒烟永远返回 `TRAIN` 失败。
- 修复：`stopped_for_smoke` 为真时一律归零；测试补断言 `exitCode == 0`。

### E2 证据（真实 worker CLI + 假 train.py）

`tools/e2_train_evidence.py` 实际执行 `python -m svc_bridge train --smoke`：

- exitCode **0**
- 事件 **102** 条（stage / progress / result）
- `maxStep = 100`（到上限即停）
- `reports/smoke_config.json` 已生成（eval_interval=50、log_interval=10、keep_ckpts=2）
- 证据：`evidence/E2/train-smoke.json`

仍不证明：真实 `train.py` 在 GPU 上的 loss/显存/checkpoint 行为（需运行时与权重）。

### 质量门

- `check.ps1` PASS：cargo test 92 / **pytest 96** / vitest 27 / clippy 0 / 体积。

## 2026-11-17 · Rust 侧真实作业链路（E2/E5 的进程半边）

- 新增 `bridge/host_train_e2e.rs`（Windows 专用，2 条）：
  1. `smoke_run_reaches_limit_and_reports_result`：真实 spawn `python -m svc_bridge train --smoke --smoke-steps 5` → 收到 stage/progress/result、`maxStep ≥ 5`、进程**自然退出**（`killed=false`）。
  2. `stop_kills_runner_within_grace`：慢速假 train（0.02s/步）跑到第一条进度后调用 `stop(5s)` → `killed=true` 且耗时 <10s。
- 这两条走的是生产同一套 `bridge::host::spawn` + JobObject + 事件通道，不是 mock。
- 说明：上游 train.py 是假脚本；真实 GPU 行为仍需运行时与权重（E3/H2 的同一阻塞）。
- 过程中修掉一处 clippy：`step as i64` 冗余转换。
- 质量门：`check.ps1` PASS（**cargo test 94** / pytest 96 / vitest 27 / clippy 0 / 体积）。

## 2026-11-17 · 错误码不再漂移（脚本比对进质量门）

### 做了什么

- 新增 `tools/check_error_codes.py`：扫描 `src-tauri/src`、`worker/svc_bridge`、`src` 中出现的 `SVC-\d{4}`，与 `error_codes.md` 正文双向比对；`## 3. 预留码` 区块不参与比对。
- 首次运行暴露真实漂移：文档 14 个码、代码 29 个码；文档里 4 个码（1004/1005/1006/8001）从未产生，代码里 15 个码没有文档。
- 重写 `error_codes.md`：
  - 12 类必测错误改为"类别 → 实际码 → 现象 → 自动动作 → 用例"，无专属码的三类（磁盘/ffmpeg/权限）明确标注而不是硬凑。
  - 新增 29 个在用码的完整表（含义 / 下一步 / 位置）。
  - 预留码单列，写明为何现在不发码。
- 把比对脚本接进 `scripts/check.ps1`（位于 pytest 之后），今后漂移直接卡门禁。

### 结果

```
python tools/check_error_codes.py
正文文档码 29 个；代码引用 29 个
结果：一致，无漂移。  (exit 0)
```

### 同步修掉的两处文档错误

- `SVC-1005` 被误写在正文说明里（其实未实现）→ 改指向预留节。
- `SVC-7005` 文档有、代码没有 → 删除；补上代码里真实存在的 `SVC-2003`（资产清单签名/公钥无效）。

### 质量门

- `check.ps1` 新增 `error codes` 步骤，PASS（94 / 96 / 27 / clippy 0 / 体积）。

## 2026-11-17 · 验收台账自检（证据可达 + 汇总一致）

- 新增 `tools/check_verification.py`：校验 `verification.md` 里引用的 `evidence/*` 文件确实存在，并把表格中 ✅/🟡/⛔ 的**实际条数**与顶部汇总比对。
- 首次运行即抓到漂移：声明 26/15/8，实际 24/16/9 —— 台账虚高，已按实际重算为 **23/17/9**（E5 从"✅ 部分"改为 🟡，因为显存释放确实没测）。
- 补齐证据引用：A2 两张截图、D1/E2 的 JSON、G2 用例结果、H5 安装包元数据（新增 `evidence/H5/installer.json`，记录 211.55MB 与 sha256，并写明"不含运行时、不作 H1 证据"）。
- 修复脚本在 GBK 控制台下打印 emoji 崩溃的问题（改 ASCII 标签 + 强制 utf-8 输出）。
- 已接入 `scripts/check.ps1`（pytest 之后）：台账再虚高就直接卡门禁。
- 质量门：PASS（94 / 96 / 27 / 台账一致 / 错误码零漂移 / clippy 0 / 体积）。

## 2026-11-17 · A3 合规材料可验证（SBOM / 许可证 / 源码包）

### 新增能力

- `release.ps1` 支持 `-SkipBuild`：不构建安装包也能生成合规材料（SBOM、licenses、source zip），并把 `check_compliance.py` 作为**发布前自检**，失败即抛错、不允许发布。
- 新增 `tools/check_compliance.py`：校验 SBOM 可解析且为 CycloneDX、包含 so-vits-svc 组件、AGPL 全文完整、SOURCE-OFFER 存在、源码包含关键文件且不混入 `target/node_modules/dist`。

### 实测结果（artifacts/0.1.0）

```
SBOM  CycloneDX 1.5 / 50 components / 含 so-vits-svc
AGPL  全文含 "GNU AFFERO GENERAL PUBLIC LICENSE"
源码包 8.7 MB / 578 条目 / 含 src-tauri/src/lib.rs 与 worker/svc_bridge/__main__.py
      无 src-tauri/target、无 node_modules
合规自检 PASS
```

### 修掉的两个真实问题

1. **SBOM 带 UTF-8 BOM**：`Set-Content -Encoding UTF8` 在 PS 5.1 会写 BOM，标准 JSON 解析器直接报错 —— 这正是 A3 要求的"可被解析"。改为 `UTF8Encoding($false)` 写出。
2. **源码归档把 `src-tauri/target` 打进去了**：原实现先复制到 `%TEMP%`（C 盘只剩 0.2GB，直接 `There is not enough space on the disk`）。改为 `tar --exclude` 直接打包，源码包从"不可用"变成 8.7MB。

### 边界

- 合规材料目前生成在 `artifacts/`，**尚未随 NSIS 安装包一起分发**（需要 runtime 才能出正式包），因此 A3 记为 🟡。

## 2026-11-17 · H3 门禁自测（齐全放行 / 篡改拦截 / 缺失拦截）

- `verify-dlls.ps1` 增加 `-ManifestPath`，使其可被合成数据驱动。
- 新增 `scripts/test-verify-dlls.ps1`：造 5 个关键 DLL（c10 / torch_cpu / torch_cuda / cudnn64_8 / nvrtc64_120_0）与 MANIFEST，跑三种场景：
  1. 齐全 → exit 0
  2. 改 1 字节 → exit 1（哈希不符）
  3. 删 `torch_cuda.dll` → exit 1（缺失关键 DLL）
- 结果：`evidence/H3/verify-dlls-selftest.json`（3/3 通过），并接入 `check.ps1` 的 `dll gate selftest` 步骤。
- 意义：H3 从"脚本存在"变成"脚本行为正确"——能放行也能拦住，不是只会打印 OK。
- 修掉脚本里两处真实写法错误：`Join-Path` 三参数、`New-Object Random` 传种子。
- 边界：仍未用真实 torch/CUDA 运行时验证（需要 2.2GB 下载），所以 H3 记 🟡。

## 2026-11-17 · G2 三方一致（文档 ↔ 脚本 ↔ 证据）

- 新增 `tools/check_error_cases.py`：解析 `error_codes.md` 第 1 节 12 行、`error-cases.ps1` 的 `Add-Row "G2-N"`、以及 `evidence/G2/error-cases.json`，三者 ID 集合与 PASS/SKIP 状态必须一致，缺一即失败。
- 接入 `check.ps1`（`error cases` 步骤）。

### 又修了一个"证据不可读"的坑

- `error-cases.ps1` 写 JSON 时用了 `Set-Content -Encoding UTF8`：PS 5.1 会写 **BOM**，标准 JSON 解析器直接拒绝；而如果省掉 `-Encoding`，中文会按 ANSI 写成 GBK 字节。
- 改成 `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))`；脚本本身也重新以 UTF-8 **with BOM** 落盘（PS 5.1 读无 BOM 的 UTF-8 会把中文当 ANSI）。
- 顺带把文档与脚本的类别名对齐（"路径非法"、"磁盘不足"），避免"名字不一致但其实是同一条"的假阴性。

### 结果

```
doc cases: 12; script cases: 12; evidence rows: 12
PASS=9 SKIP=3 FAIL=0; doc/script/evidence consistent
```

### 质量门（9 个步骤）

`tsc → vitest → pytest → verification ledger → error cases → error codes → dll gate selftest → bundle budget → fmt → clippy → cargo test`，全部 PASS。

## 2026-11-17 · G1 主线程响应探针（真实测量，前提已标注）

### 实现

- `src/lib/probe.ts`：以 `setTimeout(计划间隔)` 的**超出量**为延迟样本（不是把间隔本身算成延迟），可注入 IPC 压力函数；`summarize` 输出 p50/p95/max/超 200ms 计数。
- `commands/perf.rs::save_probe_result`：把结果原子写入 `数据根/logs/perf/probe-<时间>.json`（带 schema 与"不等于训练满载"的说明）。
- 触发方式：以 `SVCSTUDIO_PROBE=1` 启动时，`bootstrap` 返回 `probe=true`，AppShell 启动后跑 5s 探针（每轮调一次 `jobs_summary` 施压）并落盘。正常启动完全不走这段代码。

### 实测（`evidence/G1/probe.json`）

```
durationMs 5021   samples 227
p50 2ms   p95 3.2ms   max 4ms   over200ms 0
```

远低于 200ms 门限。**但它不是 G1 的完整证据**：G1 要求"训练满载时"，而本机无 GPU/权重，无法制造满载。测量方法与落盘机制已就绪，满载场景待运行时补齐。

### 质量门

- 全部 9 步 PASS（94 / 96 / 27 / 台账 / 用例一致 / 错误码零漂移 / DLL 3/3 / 体积 / clippy 0）。
- 过程中修了一处 `cargo fmt` 差异。

## 2026-11-17 · state 与磁盘一致性、spike 报告补齐

- 新增 `tools/check_state.py`：核对 `state.json.artifacts` 每一项是否真的存在于磁盘、23 项必需交付物是否齐全、spec/plan 是否已批准、stage 是否合理；`pending` 必须登记原因（当前 0 条）。已接入 `check.ps1`（`state artifacts` 步骤）。
- 首次运行发现 4 处状态陈旧：`adr/` 与 `spike/report.md` 仍写 pending（实际 ADR 早已完成）、`task-board` 写"WP0 全部 done"、`changelog` 写"WP0+WP1"；已按现实更新。
- 补齐 `spike/report.md`：把已做的四条 spike（进程树终止、冒烟可控、关闭到托盘、主线程探针）与**一条明确未验证**（真实 torch 推理）写清楚，并说明未验证原因与补齐路径。
- `verification.md` 汇总按 G1 新增的 🟡 重算为 23/18/8；同时修正阻断项描述里过时的"3 项通过"。
- 质量门新增第 10 个步骤；`check.ps1` PASS。

## 2026-11-17 · 发现 Windows 保留设备名缺陷；真实运行时落地

### 严重缺陷：`aux.py` 在 Windows 上是保留设备名

- 现象：`Copy-Item worker runtime/worker` 报 `找不到路径 ...commands\aux.py`，但 `Get-ChildItem` 明明能列出该文件；`Test-Path`/`Get-Item` 也说不存在。改用 `\\?\` 前缀仍失败。
- 根因：`AUX`（含任意扩展名）是 DOS 设备名，Win32 路径解析会把它当设备而不是文件。PowerShell 与 `MoveFile` 全部被劫持；Python 的 `open` 恰好能读，所以单测一直绿。
- 影响：**打包时 worker 复制中断**，内置运行时会缺 `aux` 模块，聚类/特征检索在安装版直接不可用。
- 修复：模块改名为 `extras.py`（子命令名仍是 `aux`，用户无感），同步 `__main__.py`、`test_extras.py`、`test_cli_dispatch.py`；用 Python `os.rename` 完成重命名（PowerShell 做不到）。
- 防回归：`test_cli_dispatch.py` 现在导入 `extras` 并断言 `aux` 子命令仍分发到它。

### 内置运行时（CPU 版）真实构建

- `build-runtime.ps1` 支持镜像与最小化安装：
  - Python embeddable 从 **华为云镜像** 下载：6.0 MB/s（python.org 直连实测 2.2 KB/s，差 2700 倍）；
  - pip 从清华镜像装 `torch==1.13.1`（CPU）+ numpy/soundfile/PyYAML/tqdm；
  - 自动写 `zz_svcstudio.pth`，使 `python -m svc_bridge` 在运行时内可直接用（实测 `--version` → 0.1.0）。
- `stage-runtime.ps1`：staged **11,783 个文件 / 0.88 GB** 到 `src-tauri/resources`，生成 MANIFEST（sha256）。
- `verify-dlls.ps1` 支持 CPU/CUDA 两种运行时：CPU 只要求 `c10.dll` + `torch_cpu.dll`；检测到 `torch_cuda.dll` 才追加 cudnn/nvrtc 要求。
- **实测**：真实运行时校验通过（11783 文件，CPU；关键 DLL：c10 / torch_cpu / torch_global_deps / torch_python），证据 `evidence/H3/real-runtime.json`。

### 质量门

- pytest 96（重命名后全绿）；`check.ps1` PASS。

## 2026-11-17 · 开发/发布构建分离（0.88GB 资源不再拖累测试）

- 现象：`cargo test` 的 build script 报 `拒绝访问 (os error 5)`。原因是 `tauri.conf.json` 的 `bundle.resources` 指向 0.88GB/11783 文件的 `src-tauri/resources`，dev 构建也会去枚举它。
- 修复：把资源打包挪到发布专用配置 `src-tauri/tauri.bundle.json`，`release.ps1` 用 `tauri build --config` 加载；`tauri.conf.json` 保持精简。
- 效果：`cargo test` 恢复（94 passed），发布仍可产出带运行时的安装包（**已产出 317.71 MB**，含 0.88GB 运行时，压缩后远低于 2.2GB 预算）。
- 质量门：`check.ps1` PASS。

## 2026-11-17 · H4 安装/卸载实测；两处契约缺陷

### H4 实测（真实安装包，含 CPU 运行时）

```
静默安装 33.4s → 安装目录 0.89GB
payload：svc-studio.exe / resources/runtime/python.exe / torch c10.dll
         / worker/.../extras.py / upstream/train.py / MANIFEST.json 全部存在
启动：svc-studio.exe 正常起窗（标题 SVC Studio）
卸载：目录删除、HKCU 卸载项删除、残留文件 0
```

证据：`evidence/H4/install-uninstall.json`、`evidence/H1/installed-app.png`。

### 缺陷 1：安装版找不到内置运行时

- 现象：安装后界面底部显示「运行时: 未就绪」。
- 根因：`resolve_python/resolve_worker_dir/resolve_upstream_dir` 只查 `runtime/`、`../runtime/`，**不查 `resources/runtime/`**，而安装布局正是后者。
- 修复：抽出 `pick_python/pick_worker/pick_upstream` 纯函数，候选列表加入 `resources/runtime/python.exe`、`resources/runtime/worker`、`resources/upstream`；新增 4 条断言（安装布局优先、开发布局可用、上游优先 resources、空目录返回 None）。
- 顺带发现候选顺序会让**陈旧的 `resources/runtime/worker` 盖住源码 worker**，已把源码 `worker` 提前，并刷新 staged 副本。

### 缺陷 2：doctor JSON 字段命名不一致

- Rust 期望 `generatedAt/dataRoot`（camelCase），worker 实际输出 `generated_at/data_root` → 真实运行时 `doctor_report` 会解析失败。
- 修复：Rust 侧加 `#[serde(alias = ...)]` 兼容两种命名（对已有 JSON 更稳）。

### 缺陷 3：GPU 失败项没有修复动作

- `check_gpu` 在"没有 NVIDIA 显卡"时 `fix` 为空，违反 B2「每个失败项都要有可执行修复」。已补文案；Rust 测试正是靠这条断言把它抓出来的。

## 2026-11-17 · WP7 收尾：压缩 / ONNX / 声线混合接通

- 新增 `commands/model_tools.rs`：
  - `model_compress(model_id, half)`：调 worker `modeltool compress`，校验产物后把压缩版**登记为新模型**（名字带"（压缩）"、source=compressed）。
  - `model_onnx(model_id)`：仅对**有项目**的模型可用（上游脚本按项目 `checkpoints/<name>` 布局导出），导入模型会得到可操作错误。
  - `mix_preview(models, mode)`：把选中的模型写成计划 JSON，交给 worker `mix` 校验并归一权重；不落盘、不改模型。
- worker 新增 `mix` 子命令（读 JSON 计划 → 输出校验结果）。
- 模型库 UI：每张卡片增加「压缩」「导出 ONNX」「加入混合」；选中 ≥2 个后可「校验混合」并显示归一权重或阻断原因。
- 顺手修一类反复出现的坑：PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，Python 侧读 JSON 统一改成 `utf-8-sig`（mix/infer_cli/model/preprocess/train/config 六处）。
- 验证：`cargo test` 97（新增压缩产物路径稳定性断言）；`pytest` 96；`check.ps1` PASS。

边界：压缩与混合的**真实执行**需要真实模型文件 + torch（CPU 版已随运行时提供），本机无模型权重，故只验证到"命令与校验链路"。

## 2026-11-17 · E6 OOM 自愈真正实现（之前只有检测，没有重试）

- 背景：`train.py` 只在日志里识别 OOM 并发错误事件，**没有降档重试**；E6 一直是"部分"。
- 实现（worker `run_train`）：
  - 检测到 OOM 后读取项目 `configs/config.json`，把 `train.batch_size` 减半并**原子写回**；
  - 只重试一次（`retry_oom=False` 递归），不会死循环；
  - 结果新增 `retried / oomDetected / batchSizeAfterRetry`，事件里发 warn 说明降档；
  - 重试时不再重复发 `result` 事件（`_emit_result` 控制）。
- 修掉一个真实 bug：`config_path` 是相对路径，原先按**进程 cwd** 解析，导致改写的是错误文件；现在按项目根解析。
- 验证（`worker/tests/test_train_oom.py`，2 条，真实子进程）：
  - 第一次真跑假训练脚本输出 `CUDA out of memory` → exit 1；第二次成功 5 步；
  - 断言：`oomDetected=True`、`retried=True`、`batchSizeAfterRetry=4`、配置文件里 `batch_size` 真的变成 4、只有 1 个 result 事件；
  - 配置缺 `batch_size` 时不假装重试（不进入死循环）。
- 质量门：`pytest 98` / `cargo test 97` / `check.ps1` PASS。
- 边界：真实 GPU 的 OOM 文本未验证（本机无 GPU），但检测用的是 `out of memory` 子串，与 CUDA 实际报错一致。

## 2026-11-17 · 全量自查（WP0–WP9）与训练曲线

### 自查结果

- 新增 `execution/audit.md`：逐项跑任务板里写的验收命令，抓出 **6 处状态失实**并纠正：
  - W3-3 doctor 标 todo，实际已完成（9 项实测）；
  - W6-6 OOM 自愈标 done，实际上一轮才真正实现；
  - W9-1..W9-4 标"脚本就绪"，实际运行时已构建、安装包已产出并安装成功；
  - W9-5 标"脚本就绪"，实际本机安装/卸载已验证，仅干净 VM 未做。
- 修正任务板状态与"依赖阻塞记录"（GPU / 干净 VM / 模型权重三条）。

### 顺手补的功能：loss 曲线（W6-4 / W8-7）

- 协议：`WorkerEvent::Progress` 新增 `losses` 与 `lr`（worker 一直在发，之前被静默丢弃）。
- 训练命令：每个带 loss 的 progress 落一条 `CURVE` 事件（只存数值，不存整行日志）。
- 前端：`lib/curve.ts` 解析事件 → SVG 折线；训练页每 3s 拉事件并显示"第 N 步 · loss X"。
- 验证：`vitest src/lib/curve.test.ts` 4 条（排序、坏 JSON 跳过、点数不足不画、扁平序列居中）。
- 质量门：**cargo test 97 / pytest 98 / vitest 35 / clippy 0 / check.ps1 PASS**。

## 2026-11-17 · 无障碍基础（W8-6 前半）

- 跳过链接：`AppShell` 顶部加"跳到主内容"，`<main id="main" tabIndex={-1}>`，键盘 Tab 第一下即可绕过侧栏。
- 焦点可见：全局 `:focus-visible` 描边（鼠标点击不出现，Tab 导航必须出现）。
- 对话框语义：`ConsentGate` 加 `role="dialog" aria-modal="true" aria-labelledby`，首次挂载把焦点放到第一个复选框。
- 验证：`ConsentGate.test.tsx` 断言 dialog 角色、可访问名、初始焦点（3 条）；`vitest 35 passed`。
- 未做：200% 缩放布局检查、对比度实测、屏幕阅读器走查 —— 需要人工，已在任务板标注。
- 质量门：`cargo test 97 / pytest 98 / vitest 35 / clippy 0 / check.ps1 PASS`。

## 2026-11-17 · 按 README 下载权重；doctor 只剩 GPU 两项

### 用户指出的问题

上游 README 明确列了"要下哪些模型、放到哪个目录"。之前我把"没有模型权重"当成阻塞，是不对的——权重可以按 README 获取。

### 实际执行

| 资产 | 来源 | 大小 | 落位 |
| --- | --- | --- | --- |
| ContentVec（`checkpoint_best_legacy_500.pt`） | hf-mirror（README 给的 hubert_base.pt） | 189 MB | `数据根/pretrain/` |
| RMVPE（`rmvpe.pt`） | hf-mirror | 368 MB | `数据根/pretrain/` |
| NSF-HiFiGAN（`model` + `config.json`） | hf-mirror | 56 MB | `数据根/pretrain/nsf_hifigan/` |

下载耗时：189MB/15.6s、368MB/19.7s、56MB/7s（hf-mirror ≈ 12–20 MB/s）。

### doctor 结果变化

```
之前：pass 2 / warn 2 / fail 5
现在：pass 6 / fail 2（只剩 cuda 与 gpu）
证据：evidence/B1/doctor-with-weights.json
```

也就是说：**只要换一台带 NVIDIA 显卡的机器，doctor 即可全绿**，B1/H1 的最后一块拼图是硬件而不是软件。

### 过程中修掉的三个真实问题

1. **声码器校验过严**：doctor 要求 `nsf_hifigan/checkpoint`（发行包里的附带文件），但上游加载器只用 `model` + `config.json`；真实 HF 包没有该文件 → 永远 fail。已按代码实际依赖改为只查两个文件。
2. **打包运行时忽略 PYTHONPATH**：embeddable 发行版带 `python38._pth` 时进入隔离模式，`PYTHONPATH` 不生效，模块只从 `runtime/worker` 加载。这解释了"改了源码但行为没变"，也说明 `stage-runtime.ps1` 用源码覆盖 `runtime/worker` 是必须的。
3. **项目侧找不到 pretrain**：上游用相对路径 `pretrain/...`，worker 以项目根为 cwd，而权重在数据根 → 训练/预处理一定失败。新增 `infra/pretrain.rs`：创建项目时给 `project/pretrain` 建**目录联接**指向数据根（不复制 600MB），3 条单元测试覆盖缺失/已存在/成功链接。

### 清单

- `tools/fill_asset_manifest.py`：把真实文件的 sha256/size 写进 `assets/manifest.json` 并回读核对（4 个文件全部一致，`evidence/B3/manifest-fill.json`）。
- 质量门：**cargo test 100 / pytest 98 / vitest 35 / clippy 0 / check.ps1 PASS**。

## 2026-11-17 · 数据根可迁移到任意磁盘（默认仍在用户目录）

### 背景

C 盘只剩 1.9GB，权重 586MB + 运行时 0.88GB 之后没有空间跑训练。数据根硬编码在 `%USERPROFILE%\SVCStudio` 也不行——用户换盘就得重装。

### 实现

- `infra/dataroot.rs`：解析顺序 **环境变量 `SVCSTUDIO_DATA_ROOT` → 指针文件 `%LOCALAPPDATA%\svc-studio\data-root.txt` → 默认 `%USERPROFILE%\SVCStudio`**；3 条单测（环境变量优先、空值回落、默认兜底）。
- `scripts/migrate-data.ps1 -To <path>`：停应用 → `robocopy /MOVE` → 写指针 → 校验 `studio.db` 与三件权重是否到位。
- 已执行迁移：`C:\Users\yangjianlin\SVCStudio` → **`D:\program\svcstudio`**（0.57GB，全部校验通过），C 盘从 1.9GB 回到 **2.5GB**。
- 界面实测：页脚显示 `数据目录: D:\program\svcstudio`、`运行时：内置运行时已就绪`（`evidence/WP8/data-root-d-drive.png`）。
- doctor 在新数据根：**pass 7 / fail 2（仅 cuda、gpu）**。

### 质量门

`cargo test 103 / pytest 98 / vitest 35 / clippy 0 / check.ps1 PASS`。

## 2026-11-17 · GPU 实测：RTX 5060 Ti（sm_120）与 torch 1.13/cu117 不兼容

### 事实

| 项 | 值 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5060 Ti，16311 MiB，驱动 621.14 |
| 计算能力 | **12.0（Blackwell）** |
| 内置运行时 torch | 1.13.1+cpu（无 CUDA） |

### 尝试与结果

1. 从 SJTU 镜像装 `torch-1.13.1+cu117`（2.1GB，18MB/s，120s）：`torch.cuda.is_available()` → **True**。
2. 真正跑一个 256×256 矩阵乘：**进程卡死 >10 分钟未返回**（无输出、无报错）。
3. 已回滚到 CPU 版 torch，保证应用可用。

### 根因

CUDA 11.7 的 SASS 只覆盖到 sm_86/87；RTX 50 系是 **sm_120**，没有对应 kernel，只能走 PTX JIT，而 1.13 的 PTX 目标同样低于 12.0，实际表现为挂起。要让这张卡真正训练，必须 **CUDA 12.8+（torch ≥2.7）**。

### 这意味着什么

- 不是"没装驱动"、也不是"软件没写"，而是**上游 so-vits-svc 4.1 的 torch 1.13 基线不支持 RTX 50 系**。
- 走 cu128 需要 Python ≥3.9（当前运行时 3.8.9）+ torch 2.8，而上游依赖的 `fairseq 0.12.2` 在 3.9+ 上没有可用 wheel，需要改上游（去掉 fairseq 或换 fork）——这是架构级变更，必须先经你同意。

## 2026-11-17 · 更正：这张卡能用，是 torch 1.13 不能用

前一轮我根据 cu117 卡死得出"RTX 50 系不可用"的结论，**结论下得太早**。重新检测：

### 环境事实

| 项 | 值 |
| --- | --- |
| GPU | RTX 5060 Ti，16311 MiB，**sm_120** |
| 驱动 | 621.14（CUDA UMD 13.5） |
| 本机 CUDA Toolkit | **12.8**（`nvcc` 可用） |
| 系统 Python | 3.13.16 + torch 2.15.0+cpu |
| 内置运行时 | Python 3.8.9 + torch 1.13.1+cpu |

### 决定性测试（venv + torch 2.8.0+cu128）

```
torch 2.8.0+cu128  cuda 12.8  avail True
device RTX 5060 Ti  sm 12.0  vram 16283 MB
cuda_init_s 0.09   first_matmul_s 0.22   warm_matmul_s 0.001
conv_ok (2,64,1000)
```

**GPU 完全正常**：cu128 下首次 kernel 0.22s、预热 1ms。
而 torch 1.13.1+cu117（CUDA 11.7，最高 sm_87）在这张卡上没有 kernel 可发射，表现为长时间挂起。

### 结论

- 不是硬件问题、不是驱动问题；
- 是**上游 so-vits-svc 4.1 的 torch 1.13 / Python 3.8 基线不支持 sm_120**；
- 要用这张卡训练/推理，必须换 **Python 3.11/3.13 + torch ≥2.7 + cu128**，并解决 `fairseq 0.12.2` 在 Python ≥3.9 上没有 wheel 的问题（这是主要风险点）。
- 试验用 venv 与 3.3GB wheel 保留在 `D:\program\svcstudio\tmp\`，供下一步 spike 复用。

## 2026-11-17 · 回答"CPU 能不能训练/推理"：推理可以，训练不行（上游硬禁止）

### 上游代码事实

| 能力 | 上游实现 | 结论 |
| --- | --- | --- |
| 训练 | `train.py:37` `assert torch.cuda.is_available(), "CPU training is not allowed."`，后面全是 `.cuda(rank)` + DDP | **CPU 训练被明确禁止**，要支持需重写 device 管理与去 DDP，属改上游 |
| 推理 | `inference_main.py -d/--device`；`Svc(device=None)` 自动 `cuda if available else cpu` | **CPU 推理上游原生支持** |

### 本轮补齐

- `infer.resolve_device()`：显式设备优先，否则有 CUDA 用 cuda，没有回落 cpu；报告新增 `device` 字段，用户能看到"为什么慢"。
- 批次执行把 device 真正传给 `Svc`（此前默认走自动选择，未显式传递）。
- 验证：`pytest 100`（新增 2 条：显式优先/回落、报告记录 device）；`cargo test 103`；`check.ps1` PASS。

### 说明

- 当前内置运行时是 torch 1.13.1+**cpu**，所以推理会走 CPU：功能可用，速度慢（RTF 远高于 1.5），且本机还没有可用的**模型文件**（G_*.pth），所以暂时无法跑真实推理。
- GPU 推理需要 cu128 运行时（见上一节）；那是另一条路线。
- CPU 训练不建议也不支持：即便改上游放行，so-vits-svc 在 CPU 上训练一个可用音色是"数天级"，不具备产品意义。

## 2026-11-17 · GPU 训练打通：100+ 步真实训练完成（RTX 5060 Ti）

### 结果

| 项 | 值 |
| --- | --- |
| 设备 | cuda:0，RTX 5060 Ti（sm_120，16GB） |
| 运行时 | Python 3.13.7 + **torch 2.8.0+cu128** + **fairseq-fixed 0.12.3.1** |
| 数据 | 24 条合成 12s 音频（非真实人声，只验链路） |
| 步数 | **120 步**（20 epoch × 6 step），exit 0 |
| checkpoint | G_0 / G_50 / G_75 / G_100（各约 599MB） |
| loss | 从 reference_loss 543 降到 49，全程有限、无 NaN |
| 速度 | 每个 epoch 3.31s（batch 4）≈ **0.55s/step** |
| 证据 | `evidence/E2/gpu-smoke.json`、`evidence/E2/train-100steps.log` |

### 为跑通所做的 5 处上游补丁（都记在 vendor 里）

1. `train.py`：Windows 下单卡不起 DDP（gloo 设备解析失败），`n_gpus==1` 直接单进程跑；
2. `train.py`：`net_g.module` → `_raw(net_g)`（单卡没有 DDP 包装）；
3. `train.py`：DataLoader `num_workers=0`（Windows 评估阶段子进程会意外退出）；
4. `utils.py`：3 处 `tostring_rgb()` → `buffer_rgba()`（matplotlib ≥3.10 移除旧 API）；
5. `checkpoint_utils.py`：`torch.load(..., weights_only=False)`（torch ≥2.6 默认 True 会拒绝旧 checkpoint）。

### 顺带发现的运行时坑

- embeddable 版 `._pth` 会开启隔离模式：**脚本目录与 PYTHONPATH 都不生效**。已加 `sitecustomize.py`，用 `SVCSTUDIO_UPSTREAM_DIR` 把上游目录塞回 `sys.path`；否则所有 `import data_utils` 都会失败。

### 还没做的

- E3（≥2000 步）：按 0.55s/step 估算约 18 分钟，可跑；
- E4（断点续训）：需要验证从 G_100 继续；
- 接进应用：目前是独立 spike（`runtime_gpu` + 手动命令），应用仍走 CPU 运行时。

## 2026-11-17 · E3/E4 达成：2020 步 GPU 长训 + 断点续训

| 项 | 结果 |
| --- | --- |
| E3 长训 | **2020 步**，exit 0；reference_loss 545 → 36.4；无 NaN/Inf；**峰值显存 14004 MB**（16GB 卡，batch=4） |
| E4 续训 | `Loaded checkpoint './logs\44k\G_2000.pth' (iteration 334)` → 继续到 step 2060，loss 连续（36→38），未从 0 重来 |
| 证据 | `evidence/E3/long-train.json`、`train-2020steps.log`、`resume-from-2000.log` |
| 数据 | 合成 24 条 12s 音频（只验证训练稳定性与显存，不代表音色质量） |

### 运行时接入（应用层）

- `core/runtimes.rs`：GPU 运行时发现（`resources/runtime_gpu`、`runtime_gpu`、环境变量覆盖），4 条单测；
- 设置项 `runtime_mode`（cpu/gpu）+ `runtime_status` / `set_runtime_mode` 命令；GPU 未安装时拒绝切换并给出可操作提示；
- `train_start` / `infer_start` 按模式选解释器；`worker_env` 补 `SVCSTUDIO_UPSTREAM_DIR`（sitecustomize 依赖）；
- 设置页新增运行时区块：显示当前模式、GPU 路径、一键切换；
- 修掉 `build-runtime-gpu.ps1` 的一个顺序 bug：依赖安装会把 torch 降级成 CPU 版，改为**最后强制安装 cu128**，并自动写 `sitecustomize.py`。

### 验收台账

**28 ✅ / 17 🟡 / 4 ⛔**（⛔ 只剩 F1、F3、G1、G5）。`check.ps1` PASS（cargo test 107 / pytest 100 / vitest 35）。

## 2026-11-17 · F1/F3 达成：GPU 单条推理与性能

| 指标 | 结果 |
| --- | --- |
| 输出格式 | **44100 Hz / 单声道 / 10.000s**（与输入 10s 误差 0ms） |
| 峰值 / 削波 | -6.25 dBFS / **0.0** |
| 数值 | 全部有限，无 NaN/Inf |
| F3 性能 | 5 次：8.45 / 8.58 / 8.59 / 8.85 / 11.32s，**中位 8.59s → RTF 0.859**（含启动与模型加载）；进程内 `vits use time 1.24s` → RTF 0.124 |
| 证据 | `evidence/F1/infer-metrics.json`、`sample-output.flac` |

### 过程里踩的三个坑（都是路径/依赖问题，不是模型问题）

1. `inference_main.py` 必须从**上游目录**运行（要写 `inference/chunks_temp.json`、读 `raw/`、写 `results/`）；
2. 输入必须放在 `raw/` 下且用相对文件名——传绝对路径会被拼成 `raw/D:\...`；
3. 上游 `pretrain/` 目录原本就有占位文件（`meta.py` 等），直接 `mklink` 会失败；要先把原目录改名再建联接，否则 `Model file not found`。
4. 缺 `praat-parselmouth`（`pm` F0 预测器需要），装到 GPU 运行时即可。

### 台账

**30 ✅ / 17 🟡 / 2 ⛔** —— 只剩 **G1**（训练满载时主线程延迟）和 **G5**（24h 浸泡）。

## 2026-11-17 · 补齐 nsf_hifigan（按官方 README），并修正"自检说缺"的两个原因

### 按 README 下载

README 指定 `nsf_hifigan_20221211.zip`（openvpi/vocoders release）。之前 GitHub Releases 超时，这次直连可用：

- 50.3 MB / 7.2s → 解压得到 **config.json、model、NOTICE.txt、NOTICE.zh-CN.txt**
- 已放入 `D:\program\svcstudio\pretrain\nsf_hifigan\`
- 证据：`evidence/B3/nsf-hifigan.json`

### 为什么之前显示"缺少 nsf_hifigan"

1. **doctor 要求了发行包里不存在的文件**：原实现检查 `README.md` + `checkpoint`，但官方 zip 里是 `NOTICE*.txt`，根本没有 `checkpoint`。上游加载器也只读 `model` + `config.json`。已改为只查这两个。
2. **自检用的运行时和任务用的运行时不是同一个**：`doctor_report` 固定用 CPU 运行时（torch 1.13 cpu），所以 cuda/gpu 必然 fail，看起来像"环境不全"。现在改为按 `runtime_mode` 选择，和训练/推理保持一致。

### 验证

- GPU 运行时下 doctor：`python=warn, torch=pass, cuda=pass, gpu=pass, ffmpeg=pass, disk=warn, encoder=pass, f0=pass, vocoder=pass` → **trainable=True**
- 带增强的推理实测：`Load HifiGAN: pretrain/nsf_hifigan/model`，exit 0
- 已把 `runtime_mode` 写为 `gpu`，应用重启后即用 GPU 运行时

## 2026-11-17 · 与系统 Python 彻底隔离

### 原则

软件只用自带运行时，**任何情况下都不回退到系统 `python`**。

### 改动

| 位置 | 之前 | 现在 |
| --- | --- | --- |
| `python_for_state` | 直接返回路径，找不到就用 `python` | 增加 `ensure_bundled()`：必须是绝对路径且文件存在，否则报 `SVC-5001`「内置 Python 运行时缺失」并说明“不会退回系统 Python” |
| `worker_env` | 继承宿主 `PYTHONPATH` | 只放 worker + upstream；新增 `PYTHONNOUSERSITE=1`、`PYTHONSAFEPATH=1`，隔离用户 site-packages |
| `run_json` | 内部用 `resolve_python()` | 必须由调用方传入解释器（dataset 等 4 处改为 `python_for_state`） |
| `check_remote` | 自带 `resolve_python()` | 改为参数注入，`model_check` / 导入 / 单测都传内置解释器 |
| `model_tools` | 3 处 `resolve_python()` | 改为 `python_for_state` |
| `doctor_report` | 固定 CPU 运行时 | 跟随 `runtime_mode`（GPU 模式下自检才可能全绿） |

### 验证

- 新增 4 条断言：拒绝裸 `python`、拒绝不存在的文件、接受真实绝对路径、`worker_env` 不继承宿主 `PYTHONPATH`；
- 手动验证：把 `runtime_gpu` 临时改名后跑相关断言，全部通过（说明不会偷偷降级到系统解释器）；
- 质量门：**cargo test 111** / pytest 100 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 全量重新编译

| 产物 | 路径 | 大小 | 时间 |
| --- | --- | --- | --- |
| 调试程序 | `src-tauri/target/debug/svc-studio.exe` | — | 13:48 |
| 安装包 | `src-tauri/target/release/bundle/nsis/SVC Studio_0.1.0_x64-setup.exe` | **317.76 MB** | 13:48 |
| 安装包 sha256 | `578275496f01109903117a4ae3d982b621c68bfa9a7a360f35351cca973856ff` | | |

- 版本号已统一回 `0.1.0`（Cargo.toml / tauri.conf.json / package.json 三处一致）。
- release 构建再次遇到 `os error 5`：`target/release/resources` 残留导致；清掉该目录与旧 `build/svc-studio-*` 后成功（已重复三次，属固定坑）。
- 新构建启动确认正常（pid 65268 起窗后关闭）。
- 24h 浸泡已用新构建重启（pid 89244）。
- 注意：安装包内置的是 **CPU 运行时**（0.88GB）；GPU 运行时 7.6GB 属可选包，目前只在开发目录 `runtime_gpu/`。

## 2026-11-17 · 统一构建入口：每次编译先清残留

`os error 5` 已经出现三次，根因固定是 `src-tauri/target/<profile>/resources` 与旧 `build/svc-studio-*` 残留。新增 `scripts/build.ps1` 作为唯一构建入口：

```
powershell -File scripts/build.ps1              # 清理 + 调试构建（约 13s）
powershell -File scripts/build.ps1 -Mode release # 清理 + 安装包（约 6min，含 0.88GB 资源）
powershell -File scripts/build.ps1 -Mode all
powershell -File scripts/build.ps1 -NoClean      # 增量构建
```

清理逻辑：

1. 结束本项目目录下运行中的 `svc-studio.exe`（否则 exe 被锁）；
2. 删除 `target/<profile>/resources` 与 `bundle`；
3. 删除 `target/<profile>/build/svc-studio-*`。

`release.ps1` 改为调用 `build.ps1 -Mode release`，不再维护第二套构建逻辑。

### 验证

- `-Mode debug`：13.3 秒，产物正常；
- `-Mode release`：351.9 秒，**未手工清理即成功**产出 317.75MB 安装包 —— 说明清理步骤确实解决了 `os error 5`；
- 副作用：构建会结束正在运行的应用，24h 浸泡需在构建后重启（已重启）。

## 2026-11-17 · 运行时合并：只保留 GPU 模式

### 决策

不再维护 CPU / GPU 两套运行时。**唯一运行时 = Python 3.13 + torch 2.8.0+cu128 + fairseq-fixed**。
理由：3.13 已被实测覆盖全部四条上游链路（flist / hubert_f0 / train / inference），而 3.8.9 那套其实从未跑过真实上游脚本。

### 代码简化

- 删除 `core/runtimes.rs` 与 `runtime_mode` 设置项、`runtime_status` / `set_runtime_mode` 命令、设置页运行时切换 UI；
- `python_for_state` 直接返回内置运行时并 `ensure_bundled` 校验；
- `worker_env` 固定 `SVCSTUDIO_RUNTIME_KIND=gpu`，不再按目录推断；
- doctor 的 Python 检查固定按 3.13 基线（不再按运行时类型分支）；
- 目录统一：`runtime/`（原 `runtime_gpu/`），`scripts/build-runtime.ps1` 为唯一构建脚本。

### 影响

| 项 | 之前 | 现在 |
| --- | --- | --- |
| 运行时数量 | 2（0.88GB + 7.6GB） | **1（7.6GB）** |
| 安装包 | 317MB（CPU 运行时） | 约 **3.4GB**（含 cu128） |
| 训练 | 需切 GPU 模式 | 直接可用 |
| CPU 推理 | 需切 CPU 模式 | 仍可用（torch 自动回落 CPU，实测 10s 音频 1.52s） |
| CPU 训练 | 不支持 | 不支持（上游 assert，与运行时无关） |

### 质量门

cargo test 107 / vitest 35 / typecheck PASS；安装包重建见后续记录。

## 2026-11-17 · 单安装包不可行：NSIS 32 位 2GB 限制 → 采用"瘦安装包 + 运行时包"

### 事实

把 7.6GB 运行时打进 NSIS，`makensis` 直接失败：

```
Internal compiler error #12345: error mmapping file (1916651297, 33554432) is out of range
Error failed to bundle app with makensis
```

这是 NSIS（32 位）的硬限制，与配置无关；即使压缩到 3.4GB 也超限。

### 采取的方案

| 产物 | 大小 | 说明 |
| --- | --- | --- |
| `SVC Studio_0.1.0_x64-setup.exe` | **211.6 MB** | 应用 + WebView2 离线安装器，不含 Python |
| `runtime-0.1.0.zip` | **3.51 GB** | Python 3.13 + torch 2.8 cu128 + fairseq-fixed（解压 7.6GB） |
| `scripts/install-runtime.ps1` | — | 解压运行时到数据根 `runtime/`，并跑自检 |

应用侧改动：`resolve_python` / `resolve_worker_dir` 增加**数据根**查找（`<data_root>/runtime/python.exe`），安装包与运行时包可以分开发布、分开放置。

### 实测

- 瘦包静默安装 → 2 个文件；启动正常；
- 数据根 `runtime/` 存在时，页脚显示 **“运行时：内置运行时已就绪”**（`evidence/H1/thin-install-runtime.png`）；
- 运行时包用 `tar -a -c` 生成（`Compress-Archive` 在 >2GB 时报“流太长”，PS 5.1 限制）。

### 运行时合并

- 删除 CPU 运行时（3.8.9，0.88GB），唯一运行时为 `runtime/`（Python 3.13 + cu128）；
- 移除 `runtime_mode` 设置、切换命令与设置页 UI；
- CPU 推理仍可用（torch 自动回落，实测 10s 音频 1.52s）；CPU 训练仍不支持（上游 assert）。

## 2026-11-17 · 首启运行时向导（解决"安装包不含运行时"的最后一公里）

### 实现

- `commands/runtime_setup.rs`：
  - `runtime_setup_status`：运行时是否就绪、python 路径、**自动发现** `runtime-*.zip`（数据根 / 安装目录 / 下载目录）；
  - `install_runtime_from_zip`：校验 zip → 解压到 `<数据根>/runtime` → 检查 `python.exe`；删除旧运行时失败时提示"先关闭任务并退出应用"（运行中的 worker 会占住 torch DLL）。
- 前端 `RuntimeGate`：首次启动若检测不到运行时就全屏引导——自动列出检测到的包（一键安装）或让用户选 zip；解压期间提示"约 3–5 分钟，不要关窗口"。
- 安装完成后自动放行；设置页仍保留环境自检。

### 实测

| 场景 | 结果 |
| --- | --- |
| 无运行时 + 数据根有 `runtime-0.1.0.zip` | 弹出向导并自动识别包路径（`evidence/H1/runtime-gate.png`） |
| 解压 3.51GB → 7.69GB | 39 秒（tar），`python 3.13.7 / torch 2.8.0+cu128 / cuda True` |
| 解压后重启应用 | 向导消失，页脚"运行时：内置运行时已就绪"（`evidence/H1/thin-runtime-installed.png`） |

### 踩到的坑

- 旧运行时正在被进程占用时，`remove_dir_all` 会失败并留下半套文件；已改成明确错误文案，并在解压前检测。
- `Compress-Archive` 无法处理 >2GB（"流太长"），运行时包改用系统 `tar.exe` 生成与解压，已验证 3.5GB 往返。

### 质量门

cargo test 109 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 消除黑色命令窗口

### 根因

Windows 上控制台子系统程序（python.exe / cmd.exe / tar.exe / nvidia-smi）被父进程以默认方式创建时，
系统会为它分配一个控制台窗口——用户看到的就是一闪而过的黑框。
应用里有 7 处 Rust spawn + 5 处 Python subprocess，全都没设 `CREATE_NO_WINDOW`。

### 修复

- 新增 `core/proc.rs`：`hide_console` / `hide_console_async`（`CREATE_NO_WINDOW = 0x08000000`），2 条单测；
- 覆盖 7 个 Rust spawn 点：worker 宿主、doctor、worker CLI、tar 解压、nvidia-smi、tasklist、mklink；
- 新增 `worker/svc_bridge/procwin.py`：`no_window_kwargs()`，覆盖 5 个 Python subprocess 点
  （extras / modeltool / preprocess / train 的 Popen 与 taskkill）；
- 诊断开关 `SVCSTUDIO_DOCTOR_ON_START=1`：启动即拉起一次 worker doctor，便于自动化验证子进程行为；
- 顺带修掉设置页「运行时：未就绪」的误报：`bootstrap` 现在真的用内置解释器读版本号（隐藏控制台）。

### 实测

| 场景 | 结果 |
| --- | --- |
| 启动 20 秒（触发 tasklist 对账） | 可见控制台窗口数 **0** |
| 启动 + 拉起 worker doctor（`SVCSTUDIO_DOCTOR_ON_START=1`） | worker 进程确实出现，控制台窗口数 **0** |

### 质量门

cargo test 111 / pytest 100 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 数据集导入：按钮开资源管理器 + 拖拽上传

### 新增

- `commands/dataset_import.rs`：`collect_sources`（递归收集音频、统计被拒）、`import_files`（同名同大小跳过、同名不同大小加序号、绝不覆盖）、`speaker_dir`（说话人目录回落 `spk`），3 条单测；
- `dataset_import` 命令：把选中的文件/目录复制进 `<project>/dataset_raw/<说话人>/`，返回 `imported/skipped/rejected/targetDir`；
- 前端 `DatasetDropZone`：虚线区域 + 「选择音频文件」「选择文件夹」两个按钮，同时监听 Tauri `onDragDropEvent` 支持直接拖入；
- 导入完成后自动刷新结构扫描与 QC，并显示「已导入 N 个文件（跳过 M 个重复，忽略 K 个非音频）」。

### 顺带修的 bug

`QcReport` 结构体漏了 `metrics` 字段，serde 反序列化时把它丢了，导致界面「静音比 / 削波比」永远显示 `—`。
改为透传 `serde_json::Value` 后，实测显示 `0.0%` / `0.000%`。

### 实测（真机）

| 步骤 | 结果 |
| --- | --- |
| 点「选择音频文件」 | 打开资源管理器（原生对话框） |
| 选中 12s wav 导入 | 复制到 `dataset_raw/qingqingzi/a0.wav`，提示「已导入 1 个文件」 |
| 自动 QC | 总时长 12 秒 / 文件数 1 / 静音比 0.0% / 削波比 0.000%，门禁「通过」并给出 SVC-3004 低数据量警告 |

### 质量门

cargo test 114 / pytest 100 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 点击项目卡片打开项目目录

### 改动

- Rust `project_open_dir(id)`：按项目 id 查登记的 `root_path`，校验目录存在后用资源管理器打开（`explorer` + 隐藏控制台）。只允许打开已登记的项目目录，不接受任意路径。
- 前端项目卡片：点击 = 设为当前项目 **并**打开其目录；卡片内加一行「点击卡片打开项目目录」提示；删除按钮 `stopPropagation` 不受影响。
- 2 条单测：不存在目录返回 `SVC-7004`、存在目录可正常打开。

### 实测

点击 `qingqingzi` 卡片后，资源管理器窗口标题变为 **「qingqingzi - 文件资源管理器」**（pid 96180），路径即项目根目录。

### 质量门

cargo test 116 / pytest 100 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 项目目录改到软件安装位置

### 变更

- 新增 `core/projects_root.rs`：项目根目录解析顺序 = 环境变量 `SVCSTUDIO_PROJECTS_ROOT` → 设置项 `projects_root` → **安装目录下的 `projects/`（可写时）** → 数据目录兜底；
- 用「真的写一个临时文件再删」判断目录可写，避免 `C:\Program Files` 这类无权限目录导致建项目直接失败；
- `create_project` 默认根目录改为上述解析结果；原先禁止把项目放在程序子树的规则保留（仍禁 `runtime/` `assets/` `configs_template/`）；
- `bootstrap` 增加 `projectsRoot`，设置页显示「项目目录」；4 条单测覆盖优先级与可写探测；
- 新增 `src-tauri/nsis-hooks.nsh`：交互式卸载前提示「projects 里是数据集与模型」，可取消；静默卸载跳过提示。

### 实测

| 项 | 结果 |
| --- | --- |
| 装到 `tmp\verify-install2` 后新建项目 | 落在 `verify-install2\projects\install-dir-test` ✅ |
| 数据库 root_path | `D:\program\svcstudio\tmp\verify-install2\projects\install-dir-test` |
| 旧项目 `qingqingzi` | 仍在原数据目录（绝对路径不变，继续可用） |
| 卸载后 | 程序文件被清理，**`projects` 文件夹保留**（用户数据不丢） |

### 注意

本项目目录位置变化只影响**新建**项目；已有项目仍指向旧路径，需要迁移的话得手工移动 + 改库（暂未提供一键迁移）。

## 2026-11-17 · 主进程也不再带控制台（彻底消除黑框）

### 根因（第二次）

之前修的是**子进程**（python/cmd/tar）的黑框；这次截图里的是**应用自身**的控制台：
`main.rs` 写的是 `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]`，
即 **debug 构建是控制台子系统**，从终端启动就会附着/新建控制台，tracing 的 INFO 全打在里面。

### 修复

- `main.rs` 改为无条件 `#![windows_subsystem = "windows"]`，debug 与 release 都不再有控制台；
- 日志改为落盘 `<数据目录>/logs/app.log`（`LogFile` 实现 `MakeWriter`），不再依赖 stdout；
  需要实时输出时设 `SVCSTUDIO_LOG_STDOUT=1`；
- 启动仍在文件里留一行 `---- app start ----`，方便排查。

### 实测

| 场景 | 控制台窗口数 |
| --- | --- |
| 启动 debug 构建（`target/debug/svc-studio.exe`） | **0** |
| 安装 release 包并启动（从有控制台的父进程） | **0** |
| 日志 | `D:\program\svcstudio\logs\app.log` 正常写入 |

### 质量门

cargo test 120 / pytest 100 / vitest 35 / clippy 0 / `check.ps1` PASS。

## 2026-11-17 · 代码与数据合并到同一个软件目录

### 规则变更

数据根默认值从 `%USERPROFILE%\SVCStudio` 改为**软件安装目录（可写时）**，即：

```
<软件目录>\
  svc-studio.exe  uninstall.exe
  runtime\      Python 3.13 + torch cu128
  pretrain\     ContentVec / rmvpe / nsf_hifigan
  projects\     项目（数据集、配置、模型）
  logs\         app.log
  studio.db
```

解析顺序：环境变量 `SVCSTUDIO_DATA_ROOT` → 指针文件 → **安装目录（写探测通过）** → `%USERPROFILE%\SVCStudio` 兜底。

### 配套改动

- `pick_data_root_with()` 增加 install_dir 参数，用真实写文件探测可写性；
- `scripts/dev.ps1` 把开发数据固定到仓库内 `.devdata`（gitignore），避免跟随 `target/debug`；
- 清理：`D:\program\svcstudio\tmp`（8.6GB 临时数据）与 `runtime-0.1.0.zip`（3.5GB 冗余包）已删除；指针文件已移除。

### 实测

| 项 | 结果 |
| --- | --- |
| 安装位置 | `D:\program\svcstudio`（与数据同目录） |
| 应用启动日志 | `db=D:\program\svcstudio\studio.db` |
| 运行时 | 安装目录下 `runtime\python.exe` 被识别（页脚 3.13.7） |
| 权重 | 安装目录下 `pretrain\` 被识别（自检 vocoder 通过） |
| 新建项目 | `root_path=D:\program\svcstudio\projects\qingqingzi`，目录已创建 |

### 注意

- 若装到 `C:\Program Files` 等不可写位置，会自动退回 `%USERPROFILE%\SVCStudio`，设置页「数据目录」会显示实际位置；
- 卸载仍然保留 `projects`（用户数据不删）。
