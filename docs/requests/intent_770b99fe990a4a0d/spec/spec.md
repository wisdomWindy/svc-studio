# Spec：SVC Studio（so-vits-svc 4.1-Stable 桌面端）

| 项 | 值 |
| --- | --- |
| 版本 | v0.1 |
| 交付单元 | `intent_770b99fe990a4a0d`（单交付单元） |
| 批准状态 | 框架自动批准（依据 §17 追溯、§16 假设、PRD/需求/设计三件套一致） |
| 上游 | [PRD v0.6](../PRD.md)、[页面设计](../design/page-design.md)、[需求拆分](../requirement-splitting/requirements.md)、[追溯矩阵](../requirement-splitting/traceability.md)、[附录 A/B/C](../prd/) |

## 1. 文档定位

本文件把 PRD 的**产品规则**翻译成**可实现的系统契约**：进程边界、消息协议、状态机、错误对象、并发与资源、性能预算、合规实现点。

**权威关系**：PRD 定义"做什么与为什么"，本 spec 定义"系统必须遵守什么"，architecture-design 定义"代码怎么组织"，plan 定义"先做什么"。四者冲突时以 PRD 为准并回滚修订。

本 spec **不重复** PRD 的功能细节，只补齐 PRD 未定义到可实现粒度的契约。

## 2. 目标与成功判据

- 目标：在**未安装 Python** 的 Windows 11 + NVIDIA（≥6GB）机器上，安装后完成「导入数据 → 预处理 → 100 步冒烟 → 长训 → 试听导出」；并支持外部模型拖入即推理、输出落独立批次目录。
- 成功判据：验收清单 A1…I2 全通过（[traceability](../requirement-splitting/traceability.md)），阻断项 A1/A3/B1/C5/E2/E5/F1/H1/H2/H4 不允许豁免。
- 性能判据：冷启动可交互 ≤1.5s（底线 2.5s）、路由 ≤100ms、IPC 往返 P95 ≤10ms、训练满载长任务 0 个 >50ms、取消 ≤2s、RTF ≤1.5（GPU 决定，不劣化）、安装包 ≤2.2GB。
- 质量判据：P0/P1 缺陷 0；静态检查零警告；单测覆盖 Rust ≥85% / Python ≥90% / 前端关键路径 ≥80%；三个解析器 fuzz ≥10⁶ 次无 panic；性能回归 >5% 阻断。

## 3. 运行形态

| 维度 | 契约 |
| --- | --- |
| 客户端形态 | **Tauri 2 应用**：Rust 核心（进程/DB/文件/校验）+ WebView2 前端（React + TypeScript），Python 只作计算 worker |
| 用户模型 | 单机单用户；无账号、无多租户、无远程调用 |
| 实例 | 单实例；第二次启动激活已有窗口（不做多开） |
| 数据归属 | 全部落本机；`%LOCALAPPDATA%\SVCStudio\studio.db` + 用户目录下的项目/模型 |
| 离线 | 默认零外联；仅"资产下载"与"检查更新（默认关）"可联网，且必须用户点击触发 |
| 权限 | 普通用户即可运行；不需要管理员（安装到 Program Files 时由安装器提权，运行期不提权） |
| 多项目并发 | 允许；但**同一项目同一时刻只允许一个写任务**（§10.1），跨项目训练仍共用一张 GPU（§10.2） |

## 4. 术语

| 术语 | 含义（本文严格使用） |
| --- | --- |
| 项目 / workspace | 一个目录，含 `configs/ filelists/ dataset_raw/ dataset/ logs/ raw/ results/ reports/`，是上游脚本的 cwd |
| 批次 / batch | 一次推理的输出单元，落 `imports/<slug>/outputs/<yyyyMMdd-HHmm>_<名称>/` 或项目 `results/<批次>/` |
| 任务 / job | 一次可观测的作业，有 `job_id`、状态机、事件流、报告 |
| 产物 / artifact | 任务写出的文件（wav/soft/f0/spec/pth/报告） |
| 外部模型 | 非本软件训练、经上传区导入的模型（`source=imported`） |
| 冒烟 / smoke | 真实 `train.py` 跑 100 步的短训练，用于验证 loss/落盘/续训/事件四件事 |
| 登记 / register | 把 checkpoint 写入模型库（`models` 表 + 卡片） |

## 5. 范围

见 [scope.yaml](../scope.yaml)。spec 层面额外明确：

- **做**：单组模型导入、两个弹窗、外部模型推理、批次输出、诊断包、声线混合（基础版）。
- **不做**：批量模型导入、ONNX 推理、云端、账号、多语言 UI（首发仅简体中文）、macOS。

## 6. 系统上下文与进程模型

```
┌──────────── SVCStudio.exe（Tauri 主进程，Rust）────────────┐
│ WebView2 渲染进程（React + TS） ← 只做渲染与输入，无系统权限 │
│        ↕ Tauri IPC：command 请求/应答 + Channel 单向流       │
│ Rust 核心（tokio 多线程）                                    │
│   ├─ JobManager（状态机、队列、SQLite 事务）                 │
│   ├─ ProcessHost（Job Object 绑定、超时、杀进程树）           │
│   ├─ EventHub（Channel 批量 → 前端 store）                   │
│   └─ AssetVerifier / DoctorRunner / Importer                 │
└───────────────┬──────────────────────────────────────────────┘
                │ stdin/stdout JSON-Lines（§6.2）
┌───────────────▼──────────────┐   ┌────────────────────────────┐
│ Python worker（每 job 一个） │   │ 上游脚本（train.py 等）     │
│  svc_bridge（我们的代码）     │──▶│ 以子进程方式被 bridge 拉起   │
│  只做参数装配/事件解析/落盘    │   │ cwd = 项目根               │
└──────────────────────────────┘   └────────────────────────────┘
```

- **渲染进程零阻塞、零权限**：WebView 没有文件系统与进程 API；所有 IO 必须经 Rust command。Rust 侧禁止在 IPC handler 里做 >16ms 的同步工作（sha256、目录遍历、DB 批量写）——这些要么 `spawn_blocking`，要么下沉到 worker。
- **worker 崩溃不影响主进程**：`ProcessHost` 用 Windows Job Object 绑定进程树，退出时统一回收。
- **上游脚本不被 import 进主进程**（除轻量校验）：Rust 侧不加载 torch，Python 只在 worker 里初始化，避免拖慢冷启动。

### 6.1 进程种类

| 进程 | 生命周期 | 用途 |
| --- | --- | --- |
| `SVCStudio.exe`（Tauri） | 应用存活期 | Tauri 壳 + Rust 核心（编排、DB、文件、校验）+ WebView2 宿主 |
| WebView2 渲染进程 | 应用存活期 | React UI；**无文件/进程权限**，只经 IPC |
| `python.exe worker.py --job <json>` | 单任务 | 所有重活：预处理、训练、推理、导入、校验 |
| 上游 `python train.py` 等 | 单步 | 由 worker 以子进程拉起；worker 负责解析日志 |

### 6.2 主进程 ↔ worker 协议（JSON Lines）

- 传输：worker 的 `stdin` 收命令，`stdout` 发事件，**每行一个 JSON 对象**，UTF-8，`\n` 分隔。
- 命令：`{"id":"c1","method":"start|cancel|ping","params":{...}}`
- 应答：`{"id":"c1","ok":true,"result":{...}}` 或 `{"id":"c1","ok":false,"error":{...}}`
- 事件：`{"type":"stage|progress|log|metric|artifact|error|result|heartbeat","job_id":"...","seq":12,"ts":"ISO8601",...}`
- `log` 事件带 `{"level":"info|warn|error","source":"upstream|bridge","line":"..."}`；原始行同时写 `logs/jobs/<job_id>.log`。
- 心跳：每 2s 一次 `heartbeat`（含 `pid`、`rss_mb`）；15s 无心跳视为 `interrupted`。
- 取消：主进程发 `cancel` → worker 先优雅收尾 ≤4s → 未退出则杀 Job Object；主进程再校验显存回落。
- 版本：握手时 worker 上报 `{"bridge":"1.0","python":"3.8.9","torch":"..."}`，不匹配直接拒绝启动（SVC-1003）。

### 6.3 进程退出码约定

| 码 | 含义 | 主进程处理 |
| --- | --- | --- |
| 0 | 正常完成 | `succeeded` |
| 2 | 前置校验失败（参数/资产/数据） | `failed` + 具体错误码 |
| 3 | 用户取消 | `cancelled` |
| 4 | OOM | 触发 §11.3 自愈 |
| 5 | 上游脚本非零退出 | `failed`，附 exit code 与尾部日志 |
| 6 | bridge 自身异常 | `failed` + SVC-7002 + 诊断包提示 |

## 7. 功能契约

> 每条只写 PRD 未定到可实现粒度的部分；行为细节以 PRD 为准。

### F-01 项目与工作区（PRD §5.1）

- 创建项目时一次性建目录、复制 `configs_template/` 到 `<project>/configs_template/`（上游脚本按相对路径读取，必须存在），写 `project.json` + `manifest.json`（目录清单与模板 sha256）。
- 应用升级后若模板版本变化：不覆盖已有 `configs/config.json`，只更新 `configs_template/` 并在 P1 提示"模板已更新，重跑预处理会使用新模板"。
- 删除项目：先停止该项目所有任务 → 删 DB 记录（事务）→ 删目录（可失败，失败则记录 `orphan` 并提示手动清理）。
- 迁移：更新 `root_path`，校验 7 个关键目录存在，旧路径写 `previous_path`。

### F-02 doctor（PRD §5.2）

- 入口：`SVCStudio.exe --doctor --json`（GUI 同源调用同一实现），输出 §8.4 结构。
- 9 项检查 + 1 告警项（底模）；每项 `{id, status, detail, fix}`；`fix` 是结构化动作 `{kind:"open_assets"|"copy_cmd"|"open_settings", payload}`。
- 缓存：结果带 `inputs_hash`（torch 版本 + GPU + 权重 mtime），10 分钟内复用；权重变更即失效。
- 判定：任一 `required=true` 的检查 `fail` → 训练入口 `blocked`（UI 置灰 + 原因）。

### F-03 资产（PRD §5.3）

- 清单：`manifest.json` + `manifest.sig`（Ed25519，公钥内置）；校验失败进入只读模式（SVC-2003）。
- 状态：`missing/downloading/verifying/ready/corrupt`；`corrupt` 一律不可用于训练。
- 下载：HTTP Range 续传；单片 8MB；并发 2；失败指数退避 3 次；镜像按清单顺序。
- 导入：按 `(filename, size)` 匹配，再校验 sha256；允许手动映射（同名不同名）但**不自动改名**。
- 目标路径：`<assets_root>/<kind>/<key>/...`；`assets_root` 可在设置中改，改后需重校验。

### F-04 数据集与 QC（PRD §5.4）

- 导入为**整体事务**：先全量扫描 → 生成问题清单 → 用户确认后复制/转换 → 写 DB。中途失败回滚已复制的文件（按 manifest 反向删除）。
- 非 wav 转码：`ffmpeg -i in -ar 44100 -ac 1 -c:a pcm_s16le out`，输出进 `dataset_raw/<spk>/`，原文件不动。
- QC 计算：单遍流式读取（`soundfile` 分块 8192 帧），不整段载入内存；1 万条 ≤3 分钟。
- 切片：以 `librosa.effects.split(top_db=40)` 找静音段，按 `min_gap` 合并，切到 5–15s；结果写 `<orig>_<idx>.wav`，原件 `quarantine/`。
- 指纹：`sha256("\n".join(sorted(rel_path|size|sr|duration)))`，存 `datasets.fingerprint`。
- 门禁判定与豁免：结果写 `qc_reports.passed` + `blocking_count`；豁免写 `consents(kind=dataset_lowdata)`。

### F-05 预处理（PRD §5.5）

- 三步独立 job，可单步重跑；每步 `cmd`、`cwd`、`exit_code`、耗时写 manifest。
- 计数基准：`dataset/44k/**/*.wav` 数量；比对 `.soft.pt/.f0.npy/.spec.pt`（+按需 `.vol/.mel/.aug_*`）。
- 差异清单结构：`{missing:[{wav, missing:["soft","f0"]}], orphan:[...]}`。
- 单文件重跑：删除该文件对应产物 → 以该文件为输入单独跑第三步（`--in_dir` 不支持单文件，改为临时目录 + 单条 glob，跑完合并计数）。
- 配置写入：先写 `*.tmp` → Schema 校验 → `os.replace`；校验失败保留旧文件并报 SVC-4001。

### F-06 配置编辑器（PRD §5.5、附录 A）

- Schema：`config.schema.json` / `diffusion.schema.json`（随包发布，字段范围见附录 A.3）。
- 只读字段集合固定（`data.*`、模型结构字段、`spk`）；高级模式保存时比对只读字段哈希，变动即拒绝。
- 断言在保存与启动两个时点都执行（防止手改文件绕过）。
- 备份：`configs/.history/<ts>-<kind>.json`，保留 20 份，可一键回滚。

### F-07 训练（PRD §5.6）

- **新建 vs 续训**：`train.py` 启动即自动加载 `logs/44k` 最新 checkpoint。因此 UI 必须二选一：
  - 新建训练：若 `logs/44k` 存在未登记 checkpoint，则引导"归档旧模型"（移动到 `logs/archive/<ts>/44k/`）或"改为续训"；不得静默续训。
  - 续训：直接用现有 `logs/44k`，显示"从 step N 继续"。
- **冒烟**：`train.py -c .svcstudio/smoke/config.json -m _smoke`；副本配置覆盖 `eval_interval=50, log_interval=10, keep_ckpts=2`；step≥100 时 `cancel`；校验 `logs/_smoke/G_100.pth` 可 `torch.load`。冒烟产物不登记为模型，保留最近一次供排查。
- **进度**：优先解析 TensorBoard event（`logs/44k/events/`），`step` 缺失时退回日志正则 `step:\s*(\d+)`；两者都无 → 30s 后报 SVC-5004 变体"训练无输出"。
- **OOM**：`CUDA out of memory` → 降 `batch_size` 50%（≥1）→ 重启一次 → 记录 `auto_downgrade`；再失败 `failed`。
- **NaN**：loss 非有限 → 停止 → 保存最近 200 行日志 + 参数快照 → `failed(SVC-5002)`。
- **取消**：`cancel` → Job Object kill → 5s 内退出 → 显存回落校验（3s 抖动容忍）。
- **端口**：启动前探测 `configs/config.json` 的 `train.port`，被占用则 +1 至可用并写临时副本（不改用户配置）。

### F-08 推理（PRD §5.7）

- 调用方式：bridge 内 `from inference.infer_tool import Svc`，按 `inference_main.py` 的参数语义调用 `slice_inference(**kwargs)`；**不 exec 上游脚本**（避免 cwd 相对路径与 `spk_mix_map` 全局副作用）。
- 参数等价性由单测保证：同一输入 + 同一参数下，bridge 输出与 `python inference_main.py` 输出的指标（时长/峰值/包络相关）在容差内一致。
- 逐条隔离：单条异常记 `inference_items.failed` + 错误码，继续下一条；模型只加载一次。
- whisper-ppg 判定：读模型 config 的 `speech_encoder`，命中 `whisper-ppg*` 即强制 `clip=25, lg=1`（UI 锁定 + 命令日志记录）。
- 输出命名与元数据：见附录 A.9、compliance §5。
- 批次目录：`results/<批次>/`（项目内）或 `imports/<slug>/outputs/<批次>/`（外部模型）；批次名 `yyyyMMdd-HHmm[_自定义]`。

### F-09 模型库与导入（PRD §5.8、§5.9.1）

- 导入两阶段：**完整性**（1 权重 + 1 config，否则弹窗 A，0 字节写入）→ **复制**（staging，边复制边 sha256）→ **识别**（7 步，失败弹窗 B）。
- 识别产物：`compat.json`（`verdict/missing_keys/inferred_fields/shape_mismatch`）。
- 依赖资产：按 `speech_encoder` + `vocoder` + `f0_predictor` 计算 `required_assets[]`，缺失则卡 `needs_attention`，推理前二次校验。
- 状态流转：`ready ⇄ needs_attention → incompatible`；文件丢失 → `missing`（启动对账）。
- 删除：被默认模型/运行中任务/未完成任务引用 → SVC-7004 阻断；删除=删 DB 行 + 删 `imports/<slug>/model`，**保留** `outputs/`。
- 压缩/ONNX/声线混合：各自独立 job；产物登记为新模型（`source=compressed|mixed`），不回写原模型。

### F-10 任务中心（PRD §5.10）

- 事件流 append-only；UI 按 100ms 批量拉取增量。
- 重试：新建 job 复制原 `payload`，`parent_job_id` 指向原任务。
- 克隆：复制 payload 但参数可编辑后再提交。
- 孤儿对账在启动时与每 60s 各跑一次：DB `running` 但进程不存在 → `interrupted`。

### F-11 上传区（PRD §5.9.1）

- 完整性检查只读元数据：文件名、扩展名、大小、`config.json` 能否 `json.load`、pth 头部能否读（`torch.load` 仅 `map_location=cpu` 且只读 state_dict 键）。
- 完整性失败：不创建 staging、不写 DB（除一条 `import_sessions.status=incomplete` 审计记录，0 字节）。
- 复制：`models/.staging/<uuid>/` → 校验 → `os.replace` 到 `models/imports/<slug>-<sha8>/model/`。
- 冲突：sha256 相同 → 直接指向已有模型；不同 → `-2/-3` 后缀。
- 安全：未勾选"来源可信"→ 不启动导入（SVC-6009）；zip 内含脚本 → 整包拒绝（SVC-6010）。

### F-12 导出与元数据（compliance §5）

- FLAC 写 Vorbis comment；WAV 写 LIST-INFO；两者都写同名 `.json` sidecar。
- 字段集合固定 7 个（见 compliance §5）；写入失败不阻断导出，但报告记 `metadata_written=false`。

## 8. 数据契约

### 8.1 存储

- SQLite 表结构、索引、迁移见 [附录 C](../prd/appendix-c-data-model.md) §C.1–C.2、§C.5。
- 新增/补充（本 spec 定稿）：
  - `jobs.payload_json` 必须含 `{"kind": "...", "project_id": ..., "params": {...}}`，重试/克隆只依赖它。
  - `job_events.data_json` 上限 4KB，超出截断并置 `truncated=true`。
  - `import_sessions.status ∈ {incomplete, checking, copying, verifying, recognizing, ready, rejected, cancelled, failed}`。

### 8.2 文件写入

- 一律 `tmp + fsync + os.replace`；跨盘用"复制→校验→删源"。
- 目录 `mkdir(parents=True, exist_ok=True)` 后立即写 `.keep`，避免空目录被误判为"已存在"。
- 单文件 >2GB 的复制必须分块（8MB）并每块更新进度，禁止一次性 `shutil.copyfile`。

### 8.3 Job 状态机

```
blocked ──deps_ok──> queued ──start──> running ──ok──> succeeded
                                   ├─stop──> stopped ──resume──> queued
                                   ├─cancel──> cancelled
                                   ├─error──> failed ──retry──> queued
                                   └─crash──> interrupted ──resume/retry──> queued
```

- 合法迁移集合固定，非法迁移写日志并忽略（防状态回退）。
- `stopped` 仅 `train/diffusion`；其余任务的"停止"落 `cancelled`。
- `succeeded_with_errors` 只用于批量推理的**批次**汇总，不用于单任务状态。

### 8.4 doctor JSON

```json
{"schema":"svcstudio.doctor/1","generated_at":"...","inputs_hash":"sha256:...",
 "overall":"pass|warn|fail",
 "checks":[{"id":"python","status":"pass","required":true,"detail":"3.8.9",
            "fix":{"kind":"none"}},
           {"id":"cuda","status":"fail","required":true,"detail":"driver 5xx < required",
            "fix":{"kind":"copy_cmd","payload":"..."}}],
 "advisory":[{"id":"base_model","status":"warn","detail":"G_0/D_0 缺失"}]}
```

### 8.5 事件与报告

- `job_events`: `{job_id, seq, ts, level, code, message, data_json}`。
- 报告结构见附录 C §C.4（qc/train/inference）；本 spec 追加 `report.schema` 必须匹配 `svcstudio.*/1`，读取方忽略未知字段。

## 9. 状态机汇总

| 对象 | 状态 | 迁移触发 |
| --- | --- | --- |
| job | §8.3 九态 | 用户操作 / 进程事件 |
| import_session | §8.1 九态 | 完整性/复制/识别结果 |
| model | `ready / needs_attention / incompatible / missing / compressed` | 识别、资产变化、文件丢失 |
| asset | `missing / downloading / verifying / ready / corrupt` | 下载、校验、篡改 |
| dataset | `draft / imported / blocked / passed / low_data` | 导入、QC、豁免 |

跨对象约束：项目 `blocked`（QC 未过）时不得创建训练 job；模型 `incompatible` 时不得创建推理 job。

## 10. 并发与资源

### 10.1 写锁

- 项目级：`.svcstudio/lock`（含 pid + 启动时间）→ 获取失败则任务进 `blocked` 并提示"该项目有任务在跑"。
- 数据库级：`BEGIN IMMEDIATE` 事务；同一事务不得跨网络/进程调用。
- 全局：`job_id` 唯一；同名输出目录自动加序号。

### 10.2 GPU 串行

- 同一时刻只允许 1 个 GPU 重任务（训练/扩散/推理/预处理特征提取）；其余排队。
- 排队规则：交互任务（推理）优先于长任务（训练），但训练一旦开始不抢占。
- 显存预算：训练前读取 `nvidia-smi` 可用显存，< 需求估算 90% 时警告。

### 10.3 队列与优先级

`interactive(推理/试听) > user(导入/预处理/冒烟) > long(训练/扩散/聚类)`；同优先级 FIFO。

## 11. 错误处理

### 11.1 错误对象

```json
{"code":"SVC-5001","severity":"recoverable","message_zh":"训练中断：显存不足。",
 "cause_zh":"batch_size=6 超过 6GB 显卡承载。","next_action":{"label":"降低参数并重试","deep_link":"project://train?batch=3"},
 "detail":"CUDA out of memory. Tried to allocate 2.00 GiB","retriable":true,
 "context":{"job_id":"...","step":1240,"vram_used_mb":6012}}
```

- `severity ∈ {blocking, recoverable, warning, fatal}`；`retriable` 决定是否显示"重试"。
- 未映射异常统一 `SVC-7999`，正文"发生了未预期错误"，附 `job_id` 与"导出诊断包"。

### 11.2 分层

| 层 | 负责 | 例子 |
| --- | --- | --- |
| 前置校验 | 编排层，任务创建前 | 缺权重、配置非法、路径不可写 |
| 运行时 | worker，事件流 | OOM、进程崩溃、产物不一致 |
| 恢复 | 编排层，任务失败后 | 续训、重跑失败项、降档重试 |

### 11.3 自动恢复策略（仅一次）

| 触发 | 动作 | 记录 |
| --- | --- | --- |
| OOM | batch/2，重启 | `auto_downgrade` |
| 端口占用 | +1 重试 | `port_changed` |
| 预处理进程池崩溃 | `num_processes` 降半重跑 | `auto_downgrade` |
| 权重校验失败 | 不重试，阻断 | — |
| 其余 | 不自动重试 | — |

## 12. 性能与容量

> **性能是功能，不是优化项**。下表目标值为发布前置条件（见 PRD §12）；plan 阶段不得下调，只能上调。每项都必须有可执行基准（`perf/` 下脚本）与入库基线。

| 场景 | 目标 | 底线 | 手段 |
| --- | --- | --- | --- |
| 冷启动 | 首帧 ≤800ms；可交互 ≤1.5s | ≤2.5s（首启 ≤6s） | Tauri 壳 + 首屏路由懒加载；torch 只在 worker 初始化；DB 迁移异步 |
| 路由切换 | ≤100ms；骨架 ≤16ms | ≤250ms | 路由级代码分割、预取、`startTransition` |
| 渲染主线程 | 训练满载 >50ms 长任务 = 0；帧 P95 ≤16.7ms、P99 ≤33ms | 事件循环 P95 <200ms | Rust 全异步；事件 100ms 合并批量；selector 切片订阅；日志/波形用 canvas + 环形缓冲 |
| IPC | command 往返 P95 ≤10ms；事件 ≥5000/s 不丢 | P95 ≤30ms | 单条 `Channel` 批量消息；零拷贝 `Uint8Array` 传波形峰值 |
| 日志 | 5k 行/分钟 0 掉帧；10 万行检索 ≤150ms | 检索 ≤300ms | 固定行高 + 虚拟滚动；Rust 侧预过滤 |
| 文件表 | 1 万行首屏 ≤120ms；滚动 60fps | ≤300ms | TanStack Virtual + 分页 SQL（200/页） |
| 波形 | 30 分钟全图 ≤1.5s | ≤3s | Rust 侧峰值降采样并缓存（多级 LOD） |
| 音频 | 起播 ≤150ms；seek ≤80ms；underrun=0 | 无爆音 | WebAudio + 预解码 + 双缓冲 |
| 取消 | UI 反馈 ≤100ms；进程树退出 ≤2s | ≤5s | Job Object + 两阶段停止 |
| 队列 | 新任务启动 ≤300ms | ≤1s | 事件驱动调度，无轮询 |
| 内存 | 常驻 ≤250MB；24h 增长 ≤3% | <10% | 环形缓冲上限、worker 退出即回收、无全局缓存无界增长 |
| QC 吞吐 | 1 万条 ≤2 分钟 | ≤5 分钟 | 流式 + 并行（CPU-2） |
| DB | <150MB/年 | <300MB | 事件保留 90 天、报告只存路径 |

**性能工程要求**
1. 每个指标有对应基准脚本，CI 上跑"小样本"，发布前跑"全样本"，结果入 `perf/baseline.json`；
2. 回归门禁：任一指标相对基线劣化 >5% → 阻断（不允许"下次再修"）；
3. 性能剖析入口常驻：`SVCSTUDIO_PERF=1` 输出 span（启动、IPC、DB、渲染），诊断包可导出；
4. 前端包体预算：首屏 JS ≤180KB gzip，单路由 chunk ≤120KB；超出即 CI 失败。

## 13. 安全与隐私

- **pickle 风险**：导入 `.pth` 前必须用户确认来源；识别阶段只允许 `torch.load(map_location="cpu")`（torch 1.13 无 `weights_only`），并在隔离 worker 中执行，崩溃/异常不波及主进程。
- **WebView2 面**：只允许加载打包内前端资源（`tauri://localhost`），CSP 禁 `unsafe-eval` 与远程脚本；禁止窗口导航到外部 URL（外链一律 `open::url` 交给系统浏览器）；release 关闭 devtools；Tauri capabilities 只开 `dialog`、`notification`、`window-state` 与自定义 command，**不启用** `shell` / `fs` 插件（文件操作全在 Rust command 内做路径校验）。
- **IPC 入参**：所有 command 参数用 serde 强类型；路径参数先 `canonicalize` 再校验根前缀；禁止把用户字符串拼进 SQL（全部参数化）。
- **路径**：所有用户输入路径 `Path.resolve()` 后校验不越出允许根；`..` 拒绝。
- **权限**：不请求管理员；写失败给 SVC-7001 并建议换目录。
- **诊断包**：白名单 + 打包前扫描（禁 `*.wav|*.pt|*.pth|*.npy`），路径脱敏。
- **日志**：不记录音频内容、不记录绝对路径以外的用户隐私；路径可用 `SVCSTUDIO_REDACT=0` 关闭脱敏（仅诊断模式）。
- **网络**：出站仅资产域名与更新源；请求头不含设备标识。

## 14. 合规实现点（AGPL）

1. 安装包 `licenses/`：AGPL 全文、第三方 CSV、`source/` 源码归档、`SOURCE-OFFER.txt`。
2. `sbom.json`（CycloneDX 1.5）由构建脚本生成，随包发布。
3. 首启四勾选 → `consents` 四条记录（`doc_version`、`app_version`、`hash`）。
4. 导出元数据 7 字段（compliance §5）。
5. 关于页显示上游 commit 与本版本补丁清单 `PATCHES.md`。

## 15. 可观测性

- 日志字段：`ts, level, job_id, kind, project, step, event, code, msg`。
- 每次任务结束写报告 + `job_events`；诊断包聚合 doctor + 版本 + 日志尾 + 配置快照 + 清单校验。
- 自检指标（写入报告，不上传）：`steps_per_sec`、`rtf`、`peak_vram_mb`、`io_wait_ratio`。

## 16. 假设与待验证（S2 spike 必须关闭）

| # | 假设 | 若不成立的退路 |
| --- | --- | --- |
| A1 | fairseq 0.12.2 + torch 1.13.1(cu117) / 2.0.1(cu118) 在 Py3.8.9 可用 | 锁 1.13.1；仍失败则降级 fairseq 或 vendor 补丁 |
| A2 | Tauri 2 能把「内置 CPython + torch/CUDA DLL + 上游源码」作为 resources 打进安装包并正常加载 | 退回"运行时安装包"分体（首启下载/离线导入运行时） |
| A3 | `inference.infer_tool` 导入不依赖 cwd 下的 `inference/chunks_temp.json` | 初始化时把该文件复制进项目 `inference/` |
| A4 | `torch.load` 能读 4.0/4.1/压缩版三种封装 | 增加 `load_legacy` 适配器 |
| A5 | 6GB 卡 batch=2 可跑 44.1k 训练（最短切片 ≤15s） | 默认 batch=1，并在推荐表下修 |
| A6 | TensorBoard event 可在训练中增量读取 | 退回纯日志解析 + 滑动平均曲线 |
| A7 | `num_processes>1` 在 6GB 上不 OOM | 6GB 强制 1 线程 |
| A8 | Win10 22H2 干净机没有 WebView2 Runtime，可用离线 Evergreen 安装包补齐（约 130MB） | 安装器检测→静默装；失败则阻断安装并给手动包 |
| A9 | Rust `Command` + Job Object 能可靠杀掉 `python → train.py` 的孙进程 | 改用 Windows `taskkill /T /F` 兜底，并记录降级 |

## 17. 验收映射

| 需求组 | spec 章节 |
| --- | --- |
| REQ-001~003（合规/范围/首启） | §14、§3、F-01 |
| REQ-004~006（doctor/资产/诊断包） | F-02、F-03、§15 |
| REQ-007~011（数据集/QC/门禁） | F-04、§8.1 |
| REQ-012~015（预处理/配置/幂等） | F-05、F-06 |
| REQ-016~023（训练/续训/取消/OOM/登记） | F-07、§10、§11.3 |
| REQ-024~031（推理/性能/参数/批量/复现） | F-08、§12 |
| REQ-032~037（响应/错误/隔离/托盘/浸泡/路径） | §3、§6、§11、§12、§13 |
| REQ-038~043（安装/离线/DLL/卸载/体积/回滚） | §13、§14、§12 |
| REQ-044（追溯/结论） | 本文 §17 + traceability |

## 18. spec 层明确不做

- 不做多语言、多主题自定义、插件系统。
- 不做远程/多机训练、断点续传训练（只续训）。
- 不做模型加密/授权校验。
- 不实现 ONNX 推理（只做导出入口，实验性）。
