# 需求拆分：SVC Studio

- 目标：把 PRD 拆成可验收的需求（`REQ-xxx`），每条绑定验收项（`A1`…`I2`），构成双向矩阵。
- 优先级规则：**P0 = 交付阻断**（对应验收清单「阻断项」+ A2 首启确认 + G2 错误可读性，共 12 条）；**P1 = 1.0 必须交付但可在评审中带风险豁免**；P2 不进本期矩阵（见 §4）。
- 编号规则：`REQ-0xx` 顺序编号，与验收 ID 的映射见 [traceability.md](traceability.md)。

## 1. 需求总表

| Epic | 需求 | 优先级 | 验收 |
| --- | --- | --- | --- |
| EP-1 范围与合规 | REQ-001 范围锁定 | P0 | A1 |
| | REQ-002 首次启动合规确认 | P0 | A2 |
| | REQ-003 AGPL 合规交付物 | P0 | A3 |
| EP-2 环境与资产 | REQ-004 环境自检与修复指引 | P0 | B1, B2 |
| | REQ-005 离线资产导入与完整性 | P1 | B3, B4 |
| | REQ-006 诊断包 | P1 | B5 |
| EP-3 数据集 | REQ-007 目录结构校验 | P1 | C1 |
| | REQ-008 QC 七项指标 | P1 | C2 |
| | REQ-009 切片与格式统一 | P1 | C3, C4 |
| | REQ-010 质量阈值门禁 | P0 | C5 |
| | REQ-011 低数据量豁免 | P1 | C6 |
| EP-4 预处理 | REQ-012 三步链路执行 | P1 | D1 |
| | REQ-013 产物一致性校验 | P1 | D2 |
| | REQ-014 配置校验与前置断言 | P1 | D3, D4 |
| | REQ-015 幂等与断点 | P1 | D5 |
| EP-5 训练 | REQ-016 显存自适应参数推荐 | P1 | E1 |
| | REQ-017 100 步冒烟 | P0 | E2 |
| | REQ-018 长训观测 | P1 | E3 |
| | REQ-019 断点续训 | P1 | E4 |
| | REQ-020 取消与资源释放 | P0 | E5 |
| | REQ-021 OOM 自愈 | P1 | E6 |
| | REQ-022 扩散/聚类/索引 | P1 | E7 |
| | REQ-023 模型登记与占用检查 | P1 | E8 |
| EP-6 推理 | REQ-024 单条推理正确性 | P0 | F1 |
| | REQ-025 峰值与削波控制 | P1 | F2 |
| | REQ-026 推理性能 | P1 | F3 |
| | REQ-027 参数面透传 | P1 | F4 |
| | REQ-028 歌声保护 | P1 | F5 |
| | REQ-029 whisper-ppg 适配 | P1 | F6 |
| | REQ-030 批量隔离与失败重跑 | P1 | F7 |
| | REQ-031 可复现 | P1 | F8 |
| EP-7 桌面与稳定性 | REQ-032 主线程响应 | P1 | G1 |
| | REQ-033 错误可读性（12 类） | P0 | G2 |
| | REQ-034 崩溃隔离 | P1 | G3 |
| | REQ-035 关闭不误杀 | P1 | G4 |
| | REQ-036 24h 浸泡 | P1 | G5 |
| | REQ-037 路径健壮性 | P1 | G6 |
| EP-8 发布 | REQ-038 干净机安装 | P0 | H1 |
| | REQ-039 离线全流程 | P0 | H2 |
| | REQ-040 DLL 完整性 | P1 | H3 |
| | REQ-041 卸载零残留 | P0 | H4 |
| | REQ-042 体积与启动预算 | P1 | H5 |
| | REQ-043 版本回滚 | P1 | H6 |
| EP-9 追溯 | REQ-044 矩阵覆盖与结论文件 | P1 | I1, I2 |

## 2. 需求详情（Given / When / Then）

### EP-1 范围与合规

**REQ-001 范围锁定**（P0 · 来源：[SRC] README 声明 + [DEC]）
- Given 项目已立项，When 交付评审，Then `scope.yaml` 的 `in` 非空、`out` 明确包含"TTS 文本合成"与"内置第三方权重"；PRD/intake 中 10 条旅程齐全；产品界面无 TTS 入口与暗示性文案。
- 验收：A1。

**REQ-002 首次启动合规确认**（P0 · [DEC] 合规）
- Given 全新配置（无 `consents` 记录），When 首次启动，Then 展示 AGPL 许可、上游使用规约、数据集授权、真人声音告知四项必选勾选；未全部勾选时训练入口不可点击；勾选后写入 `consents`（`doc_version`、`app_version`、`accepted_at`、`hash`）。
- 反例：仅勾选部分 → 仍不可进入训练；删除数据库后重启 → 重新要求确认。
- 验收：A2。

**REQ-003 AGPL 合规交付物**（P0 · [SRC] LICENSE）
- Given 构建安装包，When 检查包内容，Then `licenses/` 含 AGPL-3.0 全文、第三方许可证清单、本版本源码归档（含构建脚本）、`SOURCE-OFFER.txt`（URL + 3 年要约）；`SBOM.json` 可被 CycloneDX 解析器读取。
- 验收：A3。

### EP-2 环境与资产

**REQ-004 环境自检与修复指引**（P0 · [SRC] 依赖 + [SCENE] 基线）
- Given 干净机，When 运行 `doctor`，Then 输出 9 项检查（Python/torch/CUDA/GPU/ffmpeg/磁盘/编码器/F0/声码器）的 JSON；全部 `pass`；任一项人为破坏后可得到 `fail` + 可复制修复命令；存在训练必需项失败时训练入口阻断。
- 验收：B1、B2。

**REQ-005 离线资产导入与完整性**（P1 · [SRC] 权重清单）
- Given 断网并准备好本地权重目录，When 执行导入，Then 逐文件 sha256 校验通过、状态变 `ready`、无需联网即可列出；修改任一权重 1 字节后重扫 → 标记 `corrupt` 并提示替换；不存在的资产明确显示 `missing`。
- 验收：B3、B4。

**REQ-006 诊断包**（P1 · [DEC] 隐私）
- Given 任意状态，When 导出诊断包，Then zip 内含版本、驱动、doctor 结果、日志尾部 2000 行、配置快照；内容扫描证明不含音频/特征/权重；路径已脱敏。
- 验收：B5。

### EP-3 数据集

**REQ-007 目录结构校验**（P1 · [SRC] `dataset_raw/<spk>/*.wav`）
- Given 导入含非 wav、空目录、多层嵌套的目录，When 校验，Then 逐条列出问题与建议动作，不崩溃、不部分导入；非 wav 提供"转码后导入"。
- 验收：C1。

**REQ-008 QC 七项指标**（P1 · [SRC] 音频约束）
- Given 30 分钟测试集，When 生成报告，Then 含总时长、时长分布（min/p50/p95/max）、采样率分布、声道分布、静音比、削波占比、响度；与 ffprobe 抽样误差 ≤1%。
- 验收：C2。

**REQ-009 切片与格式统一**（P1 · [SRC] 5–15s / 44100 单声道）
- Given 3 分钟长音频与混合格式素材，When 执行切片与重采样，Then 切片落在 5–15s、最小间隔参数生效、超长/超短进待处理清单、抽样 20 个文件全部 44100Hz 单声道、原文件进 `quarantine/`。
- 验收：C3、C4。

**REQ-010 质量阈值门禁**（P0 · [SCENE] 阈值）
- Given 数据集存在超标项，When 跑阈值断言，Then 静音比 >20% / 削波 ≥0.1% / 任一条 >30s 时阻断并给出清单；全部达标时放行。
- 验收：C5。

**REQ-011 低数据量豁免**（P1 · [DEC] 冲突裁决）
- Given 10 分钟数据集，When 尝试继续，Then 提示"音色不稳、咬字差"风险并要求显式豁免；豁免记录写库；训练报告标注 `low_data=true`。
- 验收：C6。

### EP-4 预处理

**REQ-012 三步链路执行**（P1 · [SRC] 三个脚本）
- Given 通过 QC 的数据集，When 一键预处理，Then 依次执行 resample → preprocess_flist_config → preprocess_hubert_f0，日志含完整命令与参数，三步 exit code 均为 0。
- 验收：D1。

**REQ-013 产物一致性校验**（P1 · [SRC] 产物命名）
- Given 预处理完成，When 比对 manifest 计数，Then `.wav`/`.soft.pt`/`.f0.npy`/`.spec.pt` 数量一致（差异为 0），`--use_diff` 或 `vol_aug` 时对应产物同样一致；不一致时输出差异清单。
- 验收：D2。

**REQ-014 配置校验与前置断言**（P1 · [SRC] `duration` 约束）
- Given 打开配置编辑器，When 保存非法值（如 `duration` > 最短切片、`k_step_max ≥ timesteps`、类型错误），Then 拒绝保存并定位原因；合法值可回写；两份配置均过 Schema。
- 验收：D3、D4。

**REQ-015 幂等与断点**（P1 · [SRC] 逐文件幂等）
- Given 预处理中途取消，When 重新执行，Then 已完成条目跳过、产物不重复、不损坏；两次 manifest 差异仅包含未完成部分。
- 验收：D5。

### EP-5 训练

**REQ-016 显存自适应参数推荐**（P1 · [SRC] 默认 batch_size=6）
- Given 6GB 与 12GB 两张卡，When 执行探测，Then 给出不同 `batch_size`/扩散 `duration` 建议并显示依据；越界输入被拦截。
- 验收：E1。

**REQ-017 100 步冒烟**（P0 · [DEC] 防白跑）
- Given 预处理产物齐全，When 启动冒烟，Then ≤10 分钟完成；loss 有限；`logs/44k/G_{step}.pth` 落盘且可 `torch.load`；事件文件可读；随后可继续长训或续训。
- 验收：E2。

**REQ-018 长训观测**（P1 · [SRC] 日志格式）
- Given ≥2000 步训练，When 观察，Then 无 NaN/Inf；loss 曲线与 lr 可查；峰值显存有记录；进度按 step 推进。
- 验收：E3。

**REQ-019 断点续训**（P1 · [SRC] 自动加载最新 checkpoint）
- Given 第 500 步强杀进程，When 重新启动训练，Then 从最近 checkpoint 恢复；步数连续；相邻 10 步 loss 均值偏差 <20%。
- 验收：E4。

**REQ-020 取消与资源释放**（P0 · [SCENE] 5s）
- Given 训练进行中，When 点击停止/取消，Then 5s 内整个进程树退出、`nvidia-smi` 显存回落、任务状态为 `stopped`/`cancelled`，已保存 checkpoint 保留。
- 验收：E5。

**REQ-021 OOM 自愈**（P1 · [SRC] 显存敏感）
- Given 故意设置必然 OOM 的 `batch_size`，When 训练失败，Then 自动降半重启一次并成功；任务记录降档 `from/to/reason`；再次失败则停止并给结构性建议。
- 验收：E6。

**REQ-022 扩散/聚类/索引**（P1 · [SRC] 可选能力）
- Given 开启 `--use_diff` 并完成扩散训练，When 运行聚类与索引，Then 产物落在 `logs/44k/diffusion/`、`logs/44k/kmeans_10000.pt`、`logs/44k/feature_and_index.pkl`，且可被推理加载。
- 验收：E7。

**REQ-023 模型登记、外部模型导入与占用检查**（P1 · [DEC] 模型库）
- Given 训练结束，When 查看模型库，Then 模型卡含步数、config、数据集指纹、训练参数快照、关联产物；模型是全局资产（带来源项目标签），可导出/导入 `*.svcmodel.zip` 并在任意项目引用；删除时若被默认模型或运行中任务引用则阻止并说明。
- Given 一个**非本软件训练**的模型包（1 个权重 + `config.json`），When 上传，Then 通过完整性检查后进入 7 步兼容性判定：通过则登记为 `imported` 并展示依赖资产状态；失败则**弹出「无法识别该模型」**并附一句原因与缺失键清单，目录移到 `_unrecognized/`；仅缺 `enc_q` 的压缩模型判 `ready(compressed)`；非 44100Hz 训练的模型判"无法识别"；ONNX / RVC / TTS 架构给出"不支持"而非"无法识别"。
- Given 用户把文件/文件夹/zip **拖进上传区**，When 松手，Then 先做**完整性检查（≤1s，不复制）**：只有"1 个权重 + 1 个配置"才继续；随后复制到 `models/.staging/<uuid>/`（边复制边算 sha256）→ 复算比对 → 原子落位 → 识别 → 登记；**源文件全程不被修改**；源目录只读也能导入；取消或失败时清理 staging、源文件仍在。
- Given 只有模型文件、或只有 `config.json`、或两者数量不是 1:1，When 松手，Then **立即弹出「请上传模型和配置文件」**（正文指出缺少哪一类），不复制、不建 staging、不写库、不占磁盘；关闭后上传区回到 idle。
- 反例与边界：sha256 复算不一致、磁盘不足、U 盘中途拔出、同名但 sha256 不同、zip 内含 `.exe/.bat/.ps1`、未勾选"来源可信" —— 分别给出 SVC-2004 / SVC-1006 / SVC-2004 / 自动后缀 / SVC-6010 拒绝 / SVC-6009 阻断，且都不破坏已有模型。
- 验收：E8。

### EP-6 推理

**REQ-024 单条推理正确性 · 外部模型**（P0 · [SRC] `inference_main.py`）
- Given 10s 输入与已登记模型（可来自任意项目或外部模型包），When 执行推理，Then 输出 44.1kHz 单声道、时长误差 ≤100ms、无 NaN、可 A/B 播放并导出带元数据的文件；同一模型可反复调用且不修改模型。
- Given 外部导入模型，When 推理，Then 每次输出落在**独立批次文件夹**（`imports/<模型>/outputs/<yyyyMMdd-HHmm>_<批次>/`，内含音频 + `report.json` + `params.json`），且不写模型来源目录、不写项目 `results/`、不写安装目录程序子树。
- 验收：F1。

**REQ-025 峰值与削波控制**（P1 · [DEC] 峰值保护）
- Given 任意输出，When 统计采样，Then 削波占比 <0.1%、峰值不超过 0dBFS；报告记录峰值。
- 验收：F2。

**REQ-026 推理性能**（P1 · [SCENE] RTF）
- Given 基线 GPU 与 10s 输入，When 重复 5 次，Then 中位数 RTF ≤1.5；CPU 模式显示实测 RTF 与"不可训练"提示。
- 验收：F3。

**REQ-027 参数面透传**（P1 · [SRC] 推理参数）
- Given 切换 f0_predictor/trans/cluster ratio/浅扩散/enhance/响度包络，When 执行，Then 实际命令与 UI 参数一致；参数快照可一键回放。
- 验收：F4。

**REQ-028 歌声保护**（P1 · [SRC] 跑调警告）
- Given 项目素材类型为歌声，When 尝试开启 `auto_predict_f0`，Then 开关置灰且提示"会严重跑调"；命令行中该参数始终为 false。
- 验收：F5。

**REQ-029 whisper-ppg 适配**（P1 · [SRC] clip 25 / lg 1）
- Given 使用 whisper-ppg 系列模型，When 推理，Then 自动 `clip=25`、`lg=1`，且输入框锁定说明原因。
- 验收：F6。

**REQ-030 批量隔离与失败重跑**（P1 · [SCENE] 批量）
- Given 批量中混入损坏 wav，When 执行，Then 同一批次复用同一模型与同一参数快照，损坏项失败入库（含错误码与输入指纹），其余成功；结束状态为"部分成功"；可只重跑失败项。
- 验收：F7。

**REQ-031 可复现**（P1 · [DEC] 指标级）
- Given 同一模型、同一输入、同一参数快照，When 重跑，Then 时长/峰值/包络相关性在容差内一致；报告记录 `params_hash` 与两次指标 diff。
- 验收：F8。

### EP-7 桌面与稳定性

**REQ-032 主线程响应**（P1 · [SCENE] 200ms）
- Given 训练满载，When 用定时器探针测 10 分钟，Then 事件循环延迟 P95 <200ms，界面可切换页面与查看日志。
- 验收：G1。

**REQ-033 错误可读性**（P0 · [DEC] 错误三段式）
- Given 12 类错误逐一触发，When 查看提示，Then 每类都有中文"现象 + 原因 + 下一步动作"，动作可点击；错误码与附录 B 一致。
- 验收：G2。

**REQ-034 崩溃隔离**（P1 · [SCENE] 隔离）
- Given 运行中强杀 worker 进程，When 观察 UI，Then UI 存活、任务标 `failed`、可重试或续训。
- 验收：G3。

**REQ-035 关闭不误杀**（P1 · [DEC] 托盘）
- Given 训练进行中关闭主窗口，When 按设置行为处理，Then 默认最小化到托盘且任务继续；选择"退出"需二次确认并提示任务将被停止。
- 验收：G4。

**REQ-036 24h 浸泡**（P1 · [SCENE] 稳定性）
- Given 连续运行 24h 并多次跑任务，When 监控，Then 无未捕获异常、内存增长 <10%。
- 验收：G5。

**REQ-037 路径健壮性**（P1 · [SCENE] 中文路径）
- Given 安装到含中文与空格的路径，When 跑完整链路，Then 全部成功；长路径（>200 字符）给出警告而非崩溃。
- 验收：G6。

### EP-8 发布

**REQ-038 干净机安装**（P0 · [SCENE] 无 Python）
- Given 未装 Python 的 Win10/Win11 VM，When 安装并首启，Then 安装成功、doctor 全绿、无需用户安装任何运行时。
- 验收：H1。

**REQ-039 离线全流程**（P0 · [SCENE] 离线）
- Given 断网环境，When 依次执行导入→预处理→100 步训练→推理，Then 全链路成功，无网络依赖。
- 验收：H2。

**REQ-040 DLL 完整性**（P1 · [SRC] CUDA 依赖）
- Given 打包产物，When 运行依赖扫描，Then torch/CUDA/cuDNN 依赖无缺失项；缺失则阻断发布。
- 验收：H3。

**REQ-041 卸载零残留**（P0 · [SCENE] 干净卸载）
- Given 安装并运行过，When 卸载，Then 安装目录、注册表项、服务、计划任务残留为 0；用户数据目录按设置保留或提示清理。
- 验收：H4。

**REQ-042 体积与启动预算**（P1 · [DEC] 预算）
- Given 安装包，When 测量，Then 安装包 ≤7.0GB、安装后 ≤9.0GB、冷启动 ≤8s（预算值书面记录在 plan）。
- 验收：H5。

**REQ-043 版本回滚**（P1 · [SCENE] 回滚）
- Given 已发布 vN 与 vN-1 安装包，When 降级安装，Then 数据可读、任务历史不丢。
- 验收：H6。

### EP-9 追溯

**REQ-044 矩阵覆盖与结论文件**（P1 · [DEC] 门禁）
- Given 需求与验收集合，When 运行比对脚本，Then 两集合差集为空（覆盖率 100%）；`verification.md` 与 `review.md` 存在且结论为通过或有签署豁免。
- 验收：I1、I2。

## 3. 非功能指标（可测）

| 指标 | 目标 | 底线（验收） | 关联 |
| --- | --- | --- | --- |
| 冷启动 | 可交互 ≤1.5s | ≤2.5s | REQ-042 |
| 路由切换 | ≤100ms | ≤250ms | REQ-032 |
| 渲染主线程 | 训练满载 >50ms 长任务 = 0 | 事件循环 P95 <200ms | REQ-032 |
| IPC 往返 | P95 ≤10ms | ≤30ms | REQ-032 |
| 取消退出 | ≤2s + 显存回落 | ≤5s | REQ-020 |
| 推理 RTF | ≤1.5（GTX 1660，10s） | 同左 | REQ-026 |
| 24h 内存增长 | ≤3% | <10% | REQ-036 |
| 安装包 | ≤2.2GB / 安装后 ≤3.5GB | ≤2.5GB / ≤4.0GB | REQ-042 |
| 离线 | 0 外联（除主动下载） | 同左 | REQ-039 |
| 错误可读 | 12 类全覆盖三段式 | 同左 | REQ-033 |
| 缺陷 | P0/P1 = 0 | 同左 | REQ-044 |

## 4. 不进本期矩阵的 Backlog（P2，无验收项）

以下能力**明确不做**，避免范围膨胀；若用户要求，需先改 PRD 并补验收项：

1. 多说话人混训的 UI 工作流（仅保留"可导入多 spk"的最低支持）；
2. 实时麦克风变声 / 直播推流；
3. F0 曲线手工编辑器（上游由 MoeVoiceStudio 等外部工具承担）；
4. macOS / Linux 打包；
5. 模型市场、云训练、账号体系；
6. 自动混音/母带处理（只做响度包络）；
7. **批量导入多个模型**：一次只允许 1 组（1 权重 + 1 配置），多组拖入按 SVC-6008 拦截；不做"识别到 N 组，全部导入"的批量路径。
