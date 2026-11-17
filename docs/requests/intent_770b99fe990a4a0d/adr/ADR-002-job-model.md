# ADR-002：任务模型（Rust 托管 + 每任务独立 Python worker + JSON Lines）

- 状态：**已接受（2026-11-16，随 ADR-001 修订）**
- 关联：spec §6、§10、§11.3；architecture-design §6

## 1. 问题

训练/推理/预处理都是"长、重、可能崩"的作业。要在 Tauri 架构里决定：

1. 作业跑在哪个进程；
2. 主进程（Rust）如何知道进度、如何取消、如何避免被拖死；
3. 前端如何在不刷爆 WebView 的前提下看到 5k 行/分钟的日志。

## 2. 决策

**每 job 一个独立 `python.exe` 进程，stdin/stdout 走 JSON Lines，由 Rust 用 Windows Job Object 托管。**

```
前端 ──command(job.start)──▶ Rust JobManager
                                │ spawn: python worker.py --job <tmp.json>
                                │ 绑定 Job Object（KILL_ON_JOB_CLOSE）
                                ▼
                        worker（svc_bridge）
                                │ 子进程：python train.py -c ... -m 44k
                                ▼
                          上游脚本（cwd=项目根）
```

- **命令**：`{"id","method":"start|cancel|ping","params"}`；**应答**：`{"id","ok":true|false,...}`；**事件**：`{"type":"stage|progress|log|metric|artifact|error|result|heartbeat",...}`。
- **心跳**：2s 一次；15s 无心跳判 `interrupted`（spec F-10）。
- **取消**：Rust 发 `cancel` → worker 优雅收尾 ≤4s（关 DataLoader、flush TB）→ 未退则 `TerminateJobObject`；再校验显存回落。
- **退出码**：0 成功 / 2 前置失败 / 3 取消 / 4 OOM / 5 上游非零 / 6 bridge 异常（spec §6.3）。

## 3. 为什么不用别的

| 备选 | 否决理由 |
| --- | --- |
| 常驻 worker（一个进程跑所有 job） | 训练 OOM 会把进程连同后续任务一起搞死；torch 初始化后显存不释放，推理与训练互相污染 |
| Python 线程池 / asyncio 在同一进程 | GIL + torch 全局状态 + 无法强杀；一次 segfault 直接带走 UI |
| Rust 直接 `Command` 调上游脚本，不经 worker | 参数装配（20+ 个 CLI 映射）、事件解析、产物校验会用 Rust 重写一遍；且推理需要"API 级调用"而非脚本（spec F-08），只能在 Python 侧做 |
| Tauri `externalBin`（sidecar） | sidecar 要求单文件可执行；我们的 worker 必须用**同一套**内置 CPython + site-packages（torch 2.4GB），打成单 exe 反而是 PyInstaller 老路。改用 `resources/` 目录 + 绝对路径 spawn |
| gRPC / WebSocket 本地端口 | 引入端口占用（SVC-5003 已有此错误类）、防火墙提示、额外鉴权；stdio 无端口、无网络面 |
| 文件系统当队列（watcher） | 状态不可靠、事件顺序难保证、取消延迟不可控 |

## 4. 关键细节（易踩坑）

1. **stdout 只能有 JSON**：worker 里任何 `print`、tqdm、库的 banner 都会污染协议。做法：worker 启动即 `sys.stdout` 重定向到协议流，原始 stdout 备份到 `logs/jobs/<id>.raw.log`；上游子进程的 stdout 由 worker 自己读并转成 `log` 事件。
2. **大行与小行混合**：单行上限 1MB；超长截断并附 `truncated:true`。Rust 侧按 `\n` 分帧，处理跨包截断（用 `BufReader::read_line`）。
3. **背压**：worker 侧管道写满会阻塞业务线程；Rust 必须持续读，不允许"等 UI 拉取"。事件先落 `job_events`（批量 100ms 事务），再经 `Channel` 推前端。
4. **UTF-8**：Windows 中文路径/日志 → worker 启动时设 `PYTHONIOENCODING=utf-8`，Rust 侧用 `String::from_utf8_lossy` 兜底。
5. **强杀后的状态**：Job Object 关闭即杀全树；Rust 在 `Drop`/`on_exit` 里写 `interrupted`，避免"永远 running"。
6. **并发上限**：全局最多 1 个 GPU 任务 + N 个 CPU 任务（N=2），由 Rust 信号量控制（spec §10.2）。

## 5. 后果

- 正面：崩溃隔离彻底；取消可预测；显存随进程退出释放；Rust 侧状态机不承受 torch 风险。
- 负面：每个 job 冷启动 Python → `import torch` 需 3–8s。缓解：worker 进程与"开始训练"之间先做资源预检（doctor 缓存），且训练本身以小时计，启动开销可忽略；推理批量场景复用**同一 worker** 处理整个批次（一个 job = 一批，不是一条）。
