# Intake：so-vits-svc 4.1-Stable 自动化训练与推理桌面端

- request_id: `intent_770b99fe990a4a0d`
- 原始诉求：基于 https://github.com/svc-develop-team/so-vits-svc （4.1-Stable）开发一个自动训练与推理音频的 Windows 桌面软件，先产出详细 PRD。
- intake 结论：**范围可收敛，进入 requirement-analysis**。用户给出的仓库、平台、目标（自动训练 + 推理）足以形成目标契约。
- 前置输入已读取：`README_zh_CN.md`（4.1-Stable）、`requirements.txt`、`configs_template/config_template.json`、`configs_template/diffusion_template.yaml`、`inference_main.py`、`train.py`、`preprocess_flist_config.py`、`preprocess_hubert_f0.py`、`resample.py`、`compress_model.py`。

## 1. 一句话目标

把 so-vits-svc 4.1-Stable 的「手工命令行流水线」变成一台**受门禁保护的桌面产线**：导入数据 → 一键预处理 → 训练监控 → 试听导出，全离线、可中断、可续训、错误可读。

## 2. 目标用户

| 用户 | 现状痛点 | 本产品要给的东西 |
| --- | --- | --- |
| 个人创作者（有 30min+ 干净人声） | 装环境（Python 3.8.9/torch/CUDA/fairseq）就劝退；切片、f0、config 参数全靠抄教程；训练中断即报废 | 装完即用；参数有推荐值与越界校验；中断能续训 |
| 小型工作室（批量交付） | 多项目混在一起；推理参数无法复用；出错无追溯 | 项目隔离；参数预设；任务中心 + 报告 + 模型指纹 |

## 3. 平台与硬件基线

- OS：Windows 10 22H2 / Windows 11 x64（安装到含中文与空格的路径必须可用）。
- GPU：NVIDIA GTX 1660 6GB = 最低验收基线；RTX 3060 12GB = 推荐档。
- CPU：仅支持推理兜底（训练在上游被硬性禁止，见 §4 约束 C-05）。
- 磁盘：安装后预留 20GB。
- 网络：默认全离线；仅"用户主动点击下载资产"时才联网。

## 4. 事实基线（来自 4.1-Stable 源码，非推测）

| 编号 | 事实 | 对产品的直接后果 |
| --- | --- | --- |
| C-01 | 官方实测稳定基线为 **Python 3.8.9** | 运行时内置 CPython 3.8.9，不依赖用户机器 Python |
| C-02 | 项目本质是 **SVC**（歌声/语音音色转换），README 明确"无法实现 TTS，与 VITS 模型不通用" | 产品不得出现"文字转语音"入口；帮助文档必须写清 |
| C-03 | 仓库**不含任何模型权重**；contentvec/rmvpe/nsf_hifigan/G_0/D_0 需用户自取 | 必须做资产中心 + 离线导入 + 授权提示，不内置第三方权重 |
| C-04 | 预处理与推理脚本大量使用**相对路径**（`dataset_raw`、`dataset/44k`、`configs/config.json`、`filelists/*.txt`、`raw/`、`results/`、`logs/44k`） | 引入"工作区"概念：训练/预处理以项目目录为 cwd，推理以批次工作区为 cwd |
| C-05 | `train.py` 开头 `assert torch.cuda.is_available(), "CPU training is not allowed."` | CPU 机器只开推理，训练入口置灰并说明原因 |
| C-06 | `train.py` 通过 `mp.spawn(n_gpus)` 启动，`MASTER_PORT` 取自 `config.train.port`（默认 8001） | 多卡机器会按卡数拆分；产品默认锁定单卡（`CUDA_VISIBLE_DEVICES`），启动前探测端口占用 |
| C-07 | 训练无显式 `--resume`，总是自动加载 `logs/44k` 下最新 `G_*.pth`/`D_*.pth` | "续训" = 重新执行 `train.py`；"暂停" 只能是"停止 + 从最近 checkpoint 续训" |
| C-08 | 每 `eval_interval`(默认 800) 步落盘一次 `G_{step}.pth`/`D_{step}.pth`，`keep_ckpts`(默认 3) 控制保留 | 停止训练的最大进度损失 = 至多一个 eval_interval；UI 必须诚实告知 |
| C-09 | 训练日志格式固定：`Losses: [...], step: N, lr: ...` 与 `Train Epoch: x [y%]` | 进度/曲线解析契约以此为准，异常即视为训练失败信号 |
| C-10 | 预处理产物按条目幂等：`.soft.pt`/`.f0.npy`/`.spec.pt`（`--use_diff` 或 `vol_embedding` 时另有 `.vol.npy`/`.mel.npy`/`.aug_mel.npy`/`.aug_vol.npy`） | 断点续跑天然成立，产品只需做计数与完整性校验 |
| C-11 | `resample.py` 默认"响度匹配"实为**峰值归一化到 0 dBFS**（`wav /= max(abs(wav))`），并 `trim(top_db=40)` 去静音 | 有削波风险；产品默认给"保护性处理"并在 QC 里统计削波 |
| C-12 | `preprocess_flist_config.py` 每个 speaker 取 2 条作验证集，跳过 <0.3s，非 ASCII 文件名仅告警 | 中文数据集可用但需在 QC 中提示；验证集稀缺是既有事实 |
| C-13 | 推理默认 `f0_predictor=pm`，而预处理默认 `rmvpe` | **默认不一致会跑调**；产品必须以模型训练时的 F0 为准并记录在模型元数据 |
| C-14 | whisper-ppg 系列：切片必须 <30s，推理必须 `--clip 25` 且 `--linear_gradient 1` | 产品按编码器自动套用，用户不可绕过 |
| C-15 | `shallow_diffusion` 开启时 `enhance` 被禁用；`feature_retrieval` 开启时聚类被禁用并复用 `-cm/-cr` | UI 必须做互斥联动，不能把矛盾参数传下去 |
| C-16 | `diffusion.yaml.duration` 必须小于训练集最短切片；`k_step_max` 必须 < `timesteps`，`0` 为全量训练 | 保存配置时做前置断言；纯扩散推理要求扩散模型全量训练 |
| C-17 | `compress_model.py` 移除 `enc_q`，可 `--half` 存 FP16，体积约 1/3 | 模型库提供"压缩导出"，压缩后不可再训练需提示 |
| C-18 | 上游 README 已声明"即将进入 Archive 状态" | 代码必须 **vendored 锁定**，不依赖上游继续维护 |

## 5. 能力边界（scope 摘要，详见 scope.yaml）

In：环境自检与修复指引、资产中心（sha256/离线导入/断点续传）、数据集导入与 QC、切片/重采样/降噪/响度、预处理编排与产物校验、config/diffusion 可视化编辑、训练（显存自适应/冒烟/曲线/续训/取消/OOM 自愈）、推理（单条/批量/预设/A-B/聚类/检索/浅扩散/响度包络/动态声线混合）、模型库（登记/压缩/ONNX 导出/删除保护）、任务中心、打包与合规交付。

Out：TTS、云端训练、macOS/移动端首发、真人声音授权代办、实时麦克风变声、模型市场。

## 6. 10 条 P0 用户旅程（每条含可观察成功判据）

| ID | 旅程 | 成功判据（可观察、可脚本判定） |
| --- | --- | --- |
| J1 | 干净机首启 → doctor 全绿 | 未装 Python 的 Win11 上安装后启动，9 项自检全部 `pass`；全程抓包外联请求数 = 0 |
| J2 | 断网导入全部必需资产 | 断网后导入 contentvec/rmvpe/nsf_hifigan/G_0/D_0，清单全部"已就绪"，逐文件 sha256 通过 |
| J3 | 导入 30min 数据集并出 QC 报告 | 报告含 7 项指标；总时长/采样率与 ffprobe 抽样误差 ≤1%；阻断项可点击跳到修复 |
| J4 | 长音频切片与清洗 | 切片全部落在 5–15s；原文件进 `quarantine/`；重复执行产物不翻倍（幂等） |
| J5 | 一键预处理三步 | 三步成功；各产物计数差异 = 0；两份配置过 Schema；`duration` < 最短切片；命令与参数写进 manifest |
| J6 | 100 步冒烟训练 | ≤10 分钟完成；loss 为有限值；`G_100.pth` 落盘；事件文件可读；可续训 |
| J7 | 2000 步长训 + 强杀续训 | 步数连续；相邻 10 步 loss 均值偏差 <20%；报告含峰值显存 |
| J8 | 单条推理 + A/B + 导出 | 输出 44.1kHz 单声道、时长误差 ≤100ms、无 NaN、削波 <0.1%、RTF ≤1.5；导出文件含模型指纹与参数快照 |
| J9 | 批量推理含坏文件 | 19 成功 / 1 失败入库；失败清单可一键重跑；总结状态为"部分成功" |
| J10 | 模型压缩与登记 | `release.pth` 体积约为原模型 1/3 且可推理；模型库信息完整；被任务占用时删除被阻止 |

## 7. 门禁自检（G0）

- [x] 范围表 `in`/`out` 均有条目，且 TTS 被显式排除（scope.yaml）
- [x] 合规结论含 AGPL 源码提供方式与首次启动确认流程（compliance.md）
- [x] 10 条用户旅程每条都有可观察成功判据（§6）
- [x] 工作区与 state.json 已建立

## 8. 交给下一阶段的问题

- 需要用户拍板的产品决策已收敛为 5 条（见 requirement-analysis.md §7），每条都给了默认值，默认值不阻塞 PRD 评审。
