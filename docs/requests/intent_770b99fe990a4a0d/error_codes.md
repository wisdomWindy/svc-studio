# 错误码落地对照（G2）

> 规范来源：`prd/appendix-b-error-codes.md`。本文件回答三件事：用户看到什么、系统自动做什么、有没有用例证明它真的发生。
>
> 自动比对：`python docs/requests/intent_770b99fe990a4a0d/tools/check_error_codes.py`
> 复现脚本：`scripts/error-cases.ps1` → `evidence/G2/error-cases.json`

## 1. 12 类必测错误（G2 验收项）

| # | 类别 | 实际码 | 现象（第一句） | 自动动作 | 用例 |
| --- | --- | --- | --- | --- | --- |
| 1 | 缺权重 | SVC-2001 | 缺少 {编码器/底模/声码器} | 资产中心定位缺失项，训练入口阻断 | PASS（真实 doctor 输出） |
| 2 | 显存不足 | SVC-5001 / SVC-5004 | 显存不足（CUDA OOM） | 日志识别 → 自动降 batch 50% 重试一次 | PASS（推荐降档 + 日志用例） |
| 3 | 配置非法 | SVC-4001 | 配置第 N 行字段 X 不合法 | Schema 校验 + 行号定位 | PASS（`config_assert`） |
| 4 | 路径非法 | SVC-3002 | 路径不合法或越界 | `safe_join` 拒绝，不落盘 | PASS（`import` 用例 + 批次名校验） |
| 5 | 磁盘不足 | 无专属码 | 数据盘剩余 X GB（doctor warn） | 仅警告，不阻断 | SKIP（需填满分区） |
| 6 | ffmpeg 缺失 | 无专属码 | ffmpeg 不可用（doctor fail） | doctor 阻断训练 | SKIP（本机已装） |
| 7 | 进程崩溃 | SVC-7001 | 进程意外退出 | 任务 failed，可重试 | PASS（宿主崩溃用例） |
| 8 | 音频损坏 | SVC-3001 | 第 N 条音频无法解码 | 隔离到 `quarantine/` 并继续 | PASS（QC 用例） |
| 9 | 模型不匹配 | SVC-6001 | 模型与配置结构不匹配 | 判定阶段拒绝并说明差异 | PASS（兼容判定） |
| 10 | 权限不足 | SVC-7002（OS 错误映射） | 无法读取/写入文件 | 报告系统错误原文，不吞异常 | SKIP（需 ACL 构造） |
| 11 | 端口占用 | 无专属码 | 端口被占用则自动换端口 | 向后扫描 50 个候选 | PASS（`avoids_occupied_port`） |
| 12 | 版本错配 | SVC-1003 | 组件版本组合不在支持矩阵内 | 记录版本三元组，阻断训练 | PASS（doctor） |

> 说明：第 5、6、10 类的"无专属码"是**当前实现事实**，不是漏写。它们统一走 doctor 的 `fail/warn` 或 OS 错误映射；如果后续要给用户更精确的定位，再为它们各引入一个专用码（见第 3 节）。

## 2. 全部在用的错误码（代码里真实产生）

| 码 | 含义 | 用户下一步 | 主要位置 |
| --- | --- | --- | --- |
| SVC-1001 | worker 无法执行 / 未安装 torch / 内部异常 | 看设置页自检，按提示修环境 | `bridge/host.rs`、`commands/dataset.rs`、`commands/doctor.rs` |
| SVC-1003 | 版本组合不在支持矩阵 | 换回基线组合或看 doctor 修复命令 | `commands/doctor.rs` |
| SVC-2001 | 资产缺失 | 打开资产中心导入 | `commands/doctor.rs` |
| SVC-2002 | 权重校验失败（大小/哈希不符/重复） | 重新下载或替换文件 | `assets/importer.rs`、`repo/models.rs`、`commands/inference.rs` |
| SVC-2003 | 资产清单签名/公钥无效 | 重新获取官方清单，不要用被改过的清单 | `core/assets/manifest.rs` |
| SVC-2004 | 上传的文件不是单个模型 + 单个配置 | 只放 1 个权重 + 1 个 config | `core/model/import.rs` |
| SVC-2005 | 上传缺少权重或配置 | 补齐后重新上传 | `core/model/import.rs` |
| SVC-3001 | 音频无法解码 | 换文件或先转成 wav | `worker/commands/dataset.py` |
| SVC-3002 | 目录结构/参数非法 | 按提示整理后重试 | `commands/dataset.rs`、`commands/models.rs` |
| SVC-3003 | QC 缺失或未通过 | 先去数据集页跑 QC 并修阻断项 | `commands/preprocess.rs`、`worker/commands/dataset.py` |
| SVC-3004 | 总时长低于 30 分钟 | 补齐素材，或显式豁免 | `worker/commands/dataset.py` |
| SVC-3005 | 单条音频超过 30s | 切片后重跑 | `worker/commands/dataset.py` |
| SVC-4001 | 配置或参数不合法 | 按字段提示修改 | `worker/commands/config.py`、多处命令 |
| SVC-4002 | `duration` ≥ 最短切片 | 调小 duration | `commands/preprocess.rs`、`worker/commands/config.py` |
| SVC-4003 | 预处理完成但产物计数不一致 | 按差异清单重跑受影响步骤 | `commands/preprocess.rs` |
| SVC-5001 | 环境不满足 / 显存不足 | 先自检，或降 batch | `commands/train.rs`、`core/gate.rs` |
| SVC-5002 | 训练出现 NaN/Inf | 停下查数据与 F0 | `commands/train.rs`、`worker/commands/train.py` |
| SVC-5003 | 已有 checkpoint，需声明续训 | 点「继续训练」或归档 logs/44k | `commands/train.rs` |
| SVC-5004 | OOM 自动降档失败 | 手动降 duration / batch | `commands/train.rs`、`worker/commands/train.py` |
| SVC-6001 | 模型与配置结构不匹配 | 换匹配的 config 或模型 | `worker/commands/model.py` |
| SVC-6003 | 开了特征检索但没有索引 | 先建索引或关闭检索 | `worker/commands/params.py` |
| SVC-6004 | 没有可推理的输入 | 先选音频 | `commands/inference.rs`、`worker/commands/params.py` |
| SVC-6005 | 模型不完整（缺 model 段 / 缺文件） | 重新获取完整模型 | `worker/commands/model.py` |
| SVC-6008 | 上传配对数量不对 | 1 个模型 + 1 个配置 | `core/model/import.rs` |
| SVC-6009 | 未确认模型来源可信 | 勾选信任声明 | `commands/inference.rs` |
| SVC-7001 | 进程异常退出 | 重试；复现则导出诊断包 | `bridge/host.rs` |
| SVC-7002 | 任务状态非法/无法识别 | 刷新任务列表 | `commands/jobs.rs`、`job_retry.rs` |
| SVC-7003 | 读回数据失败/解析失败 | 重试；仍失败导出诊断包 | `commands/inference.rs`、`job_retry.rs`、`jobs.rs` |
| SVC-7004 | 目标不存在（项目/模型/任务） | 刷新列表后重选 | `commands/dataset.rs`、`inference.rs` |

## 3. 预留码（已规划、当前不会产生，自动比对会跳过本节）

| 码 | 计划用途 | 为什么现在没有 |
| --- | --- | --- |
| SVC-1004 | 显存低于训练下限 | 目前由 `train_recommend.trainable=false` 表达，未单独发码 |
| SVC-1005 | ffmpeg 缺失 | doctor 输出 `fail`（id=ffmpeg），未单独发码 |
| SVC-1006 | 磁盘不足 | doctor 输出 `warn`（id=disk），未单独发码 |
| SVC-8001 | 路径含中文/空格 | 由 `safe_join` 与 OS 错误处理，未单独发码 |

## 4. 自动动作的真实性

| 动作 | 是否真的执行 | 证据 |
| --- | --- | --- |
| OOM 降档 | 是（Python 训练器 + 推荐逻辑） | `test_oom_is_detected_and_reported`、`train_recommend` 用例 |
| 音频隔离 | 是（QC 移动文件到 quarantine） | `test_qc_isolates_bad_file` |
| 任务重试 | 是（`retry_job` 重新排队，非可重试状态拒绝） | `job_retry` 断言 |
| 续训确认 | 是（`should_block_start` + `resume` 参数） | 4 条断言 |
| 环境阻断 | 是（`core::gate` + 训练/预处理入口） | 4 条断言 |

## 5. 维护约定

- 新增错误码必须同时更新：`appendix-b-error-codes.md`、本文件、`error-cases.ps1`。
- 文案禁止"请联系支持"；`next_action.label` 必须指向可点击入口。
- 提交前跑 `check_error_codes.py`，文档与代码必须双向一致（本节与第 3 节不参与比对）。
