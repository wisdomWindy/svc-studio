# 附录 A：参数契约（4.1-Stable）

> 本附录是产品与上游之间的**接口合同**。产品只能使用这里列出的入口点与参数；新增参数必须在 spec 中登记。
> 所有"上游默认值"来自 4.1-Stable 源码；"产品默认值"是本产品的决策，差异必须在 §A.8 登记原因。

## A.1 入口点映射

| 用途 | 入口 | 工作目录 | 必需输入 | 产物 |
| --- | --- | --- | --- | --- |
| 重采样/单声道/峰值 | `resample.py` | 项目根 | `dataset_raw/<spk>/*.wav` | `dataset/44k/<spk>/*.wav` |
| 划分 + 生成配置 | `preprocess_flist_config.py` | 项目根 | `dataset/44k/`、`configs_template/`（只读） | `filelists/*.txt`、`configs/config.json`、`configs/diffusion.yaml` |
| 特征 + F0 | `preprocess_hubert_f0.py` | 项目根 | `configs/*`、`pretrain/` 编码器与 F0 权重 | `.soft.pt` `.f0.npy` `.spec.pt`（+`.vol.npy`/`.mel.npy`/`.aug_*`） |
| 主模型训练 | `train.py -c configs/config.json -m 44k` | 项目根 | 预处理产物、`G_0/D_0`（可选）、CUDA | `logs/44k/G_*.pth`、`D_*.pth`、`events/` |
| 扩散训练 | `train_diff.py -c configs/diffusion.yaml` | 项目根 | `--use_diff` 产物、`logs/44k/diffusion/model_0.pt`（可选） | `logs/44k/diffusion/*.pt` |
| 推理 | `inference_main.py` | 批次工作区（含 `raw/`、`results/`；模型与 config 用绝对路径传入） | 模型、config、`raw/<wav>` | `results/<name>_<key>_<spk>...<fmt>` |
| 聚类 | `cluster/train_cluster.py [--gpu]` | 项目根 | `.soft.pt` 特征 | `logs/44k/kmeans_10000.pt` |
| 特征检索 | `train_index.py -c configs/config.json` | 项目根 | `.soft.pt` 特征 | `logs/44k/feature_and_index.pkl` |
| 压缩 | `compress_model.py -c <cfg> -i <G> -o <out> [-hf]` | 项目根 | 模型 + config | `release.pth` |
| ONNX 导出 | `onnx_export.py`（需改 `path` 变量） | 项目根 | `checkpoints/<name>/model.pth` + `config.json` | `model.onnx` |

**cwd 约束**：训练/预处理以项目根为 cwd；推理以批次工作区为 cwd（模型 `-m` 与配置 `-c` 一律传绝对路径，故可跨项目引用模型）。`configs_template/` 不在项目内，由产品以只读方式提供（复制或只读挂载），worker 启动时注入 `sys.path` 指向 vendored 代码目录。

## A.2 预处理参数

| 脚本 | 参数 | 上游默认 | 产品默认 | 合法范围 | 说明 |
| --- | --- | --- | --- | --- | --- |
| resample | `--sr2` | 44100 | 44100 | 固定 | 产品不开放，避免与训练采样率脱节 |
| resample | `--skip_loudnorm` | 关 | **开**（见 A.8-4） | 布尔 | 产品自己做峰值保护，不用上游 0dBFS 归一化 |
| flist_config | `--speech_encoder` | `vec768l12` | `vec768l12` | 13 个枚举值 | 见 A.4 |
| flist_config | `--vol_aug` | 关 | 关 | 布尔 | 开启后 config 的 `vol_embedding=true`，预处理要生成 `.vol.npy` |
| flist_config | `--tiny` | 关 | 不开放 | — | 小模型实验特性，产品不暴露 |
| hubert_f0 | `--f0_predictor` | `rmvpe` | `rmvpe`（嘈杂素材建议 crepe） | crepe/pm/dio/harvest/rmvpe/fcpe | 记录进模型元数据，推理默认跟随 |
| hubert_f0 | `--use_diff` | 关 | 关 | 布尔 | 开启才生成 mel/aug 产物，扩散训练必需 |
| hubert_f0 | `--num_processes` | 1 | 按显存：6GB→1 / 12GB→2 / 24GB→4 | 0..CPU 核数 | 0 = 用满核数；每个进程都会占用 GPU 显存 |
| hubert_f0 | `-d/--device` | 自动 | 跟随设置 | cuda/cpu | CPU 模式极慢，仅调试用 |

## A.3 `config.json` 暴露字段

| 路径 | 上游默认 | 产品范围 | 说明 |
| --- | --- | --- | --- |
| `train.batch_size` | 6 | 1–32 | 按显存推荐；OOM 时自动降半 |
| `train.epochs` | 10000 | 1–100000 | 早期停止靠用户手动停 |
| `train.learning_rate` | 1e-4 | 1e-6–1e-2 | 不建议普通用户改 |
| `train.fp16_run` | false | 布尔 | 6GB 建议开；与 `half_type` 配套 |
| `train.half_type` | fp16 | fp16/bf16 | bf16 需 30 系以上 |
| `train.keep_ckpts` | 3 | 0–50 | 0 = 保留全部（吃磁盘） |
| `train.all_in_mem` | false | 布尔 | 内存远大于数据集时可开 |
| `train.segment_size` | 10240 | 固定 | 改动会改变模型结构，禁止编辑 |
| `train.log_interval` | 200 | 10–1000 | 冒烟时临时改为 10 |
| `train.eval_interval` | 800 | 50–5000 | 冒烟时临时改为 50 |
| `train.port` | "8001" | 自动分配 | 训练前探测空闲端口并写回 |
| `model.vocoder_name` | `nsf-hifigan` | `nsf-hifigan`/`nsf-snake-hifigan` | 换了要对应权重 |
| `model.vol_embedding` | false | 跟随 `--vol_aug` | 只读（由预处理决定） |
| `model.use_automatic_f0_prediction` | true | 布尔 | 关掉则推理不能 `-a` |
| `data.sampling_rate` | 44100 | 固定 | 只读 |
| `spk` | 模板占位 | 只读 | 由预处理生成，产品不允许手改 |

> **只读字段**：`data.*`、`model` 结构字段（`inter_channels`/`hidden_channels`/`upsample_*`/`ssl_dim`/`gin_channels` 等）、`spk`。UI 以只读卡片展示，高级 JSON 模式保存时校验这些字段未被改动，改动即拒绝（"改动会导致模型无法加载"）。

## A.4 编码器与产物维度

| 编码器 | `ssl_dim` | `gin_channels` | `filter_channels` | 切片硬上限 | 推理附加 |
| --- | --- | --- | --- | --- | --- |
| `vec768l12`（默认） | 768 | 768 | 768 | <30s | — |
| `vec256l9` | 256 | 256 | 768 | <30s | — |
| `hubertsoft` | 256 | 256 | 768 | <30s | — |
| `whisper-ppg` | 1024 | 1024 | 1024 | **25s** | 强制 `clip=25`、`lg=1` |
| `whisper-ppg-large` | 1280 | 1280 | 1280 | **25s** | 强制 `clip=25`、`lg=1` |
| `cnhubertlarge` | 1024 | 1024 | 1024 | <30s | — |
| `dphubert` | 768 | 768 | 768 | <30s | — |
| `wavlmbase+` | 768 | 768 | 768 | <30s | — |

> 编码器决定 config 的维度字段，**训练后不可更改**；产品在项目创建时锁定，切换需新建项目。

## A.5 推理参数

| CLI | 长参数 | 上游默认 | 产品默认 | 范围 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `-m` | `model_path` | `logs/44k/G_37600.pth` | 模型库选择 | — | 必填 |
| `-c` | `config_path` | `logs/44k/config.json` | 随模型 | — | 必填 |
| `-n` | `clean_names` | 示例文件 | 用户输入 | — | 放在 `raw/` 下 |
| `-t` | `trans` | 0 | 0 | -24..24 | 半音 |
| `-s` | `spk_list` | 示例 | config.spk | — | 多说话人必选 |
| `-cl` | `clip` | 0（自动） | 0 | 0..60 | whisper 强制 25 |
| `-lg` | `linear_gradient` | 0 | 0 | 0..5 | whisper 强制 1 |
| `-f0p` | `f0_predictor` | **pm** | **跟随训练** | 6 枚举 | 差异见 A.8-2 |
| `-a` | `auto_predict_f0` | 关 | 关（歌声禁用） | 布尔 | 歌声开启会严重跑调 |
| `-cm` | `cluster_model_path` | "" | 自动 | — | 留空时按方案取默认路径 |
| `-cr` | `cluster_infer_ratio` | 0 | 0 | 0..1 | 0 时不加载聚类 |
| `-fr` | `feature_retrieval` | 关 | 关 | 布尔 | 开则聚类禁用，`-cm/-cr` 变索引语义 |
| `-shd` | `shallow_diffusion` | 关 | 关 | 布尔 | 开则 `-eh` 失效 |
| `-dm` | `diffusion_model_path` | `logs/44k/diffusion/model_0.pt` | 随模型 | — | 浅扩散必填 |
| `-dc` | `diffusion_config_path` | `logs/44k/diffusion/config.yaml` | 随模型 | — | — |
| `-ks` | `k_step` | 100 | 100 | 1..1000 | 越大越接近扩散结果，越慢 |
| `-od` | `only_diffusion` | 关 | 关 | 布尔 | 需完整训练的扩散模型 |
| `-se` | `second_encoding` | 关 | 关 | 布尔 | "玄学"选项，默认关 |
| `-eh` | `enhance` | 关 | 关 | 布尔 | 与 `-shd` 互斥 |
| `-eak` | `enhancer_adaptive_key` | 0 | 0 | -24..24 | 仅增强器生效时可用 |
| `-lea` | `loudness_envelope_adjustment` | 1 | 1.0 | 0..1 | 越近 1 越用输出包络 |
| `-usm` | `use_spk_mix` | 关 | 关 | 布尔 | 动态声线混合 |
| `-sd` | `slice_db` | -40 | -40 | -60..-20 | 嘈杂 -30，保呼吸 -50 |
| `-ns` | `noice_scale` | 0.4 | 0.4 | 0..1 | 拼写沿用上游 |
| `-p` | `pad_seconds` | 0.5 | 0.5 | 0..2 | 抑制首尾异响 |
| `-wf` | `wav_format` | flac | flac | flac/wav | 导出格式 |
| `-lgr` | `linear_gradient_retain` | 0.75 | 0.75 | 0..1 | 自动切片的交叉保留比例 |
| `-ft` | `f0_filter_threshold` | 0.05 | 0.05 | 0..1 | 仅 crepe 生效；调低减少跑调但增加哑音 |
| `-d` | `device` | 自动 | 跟随设置 | cuda/cpu | CPU 兜底 |

## A.6 互斥与联动矩阵

| 条件 | 强制行为 | 依据 |
| --- | --- | --- |
| `-shd` 开 | `-eh` 置灰、传 false | 上游"增强器将被禁止" |
| `-fr` 开 | 聚类路径/比例切换为索引；UI 标签改名 | 上游"聚类模型将被禁用" |
| `-cr == 0` | `-cm` 强制置空（不加载任何聚类/索引） | 上游默认路径逻辑 |
| `-cr != 0` 且 `-cm` 为空 | 按 `-fr` 自动填 `kmeans_10000.pt` 或 `feature_and_index.pkl` | 上游实现 |
| 编码器 whisper-ppg* | `-cl 25`、`-lg 1` 锁定 | 上游"否则无法正常推理" |
| 素材=歌声 | `-a` 强制 false 且置灰 | 上游"会严重跑调" |
| `-od` 开 | 要求扩散模型 `k_step_max == 0` 且训练完成 | 上游"不训练整个扩散模型将无法使用仅扩散模型推理" |
| `use_spk_mix` 且 `spk_mix_map` 只有 1 个 | 自动关闭并提示 | 上游 `if len(spk_mix_map)<=1: use_spk_mix=False` |

## A.7 保存前硬断言（任一不满足即拒绝执行）

1. `diffusion.duration < min(训练集切片时长)`；
2. `diffusion.k_step_max < diffusion.timesteps`；
3. `config.train.batch_size ≥ 1`，且 ≤ 显存推荐值 × 2（超出仅警告，不阻断）；
4. `config.train.keep_ckpts ≥ 0`；`epochs ≥ 1`；
5. 所有切片 ≤ 编码器上限（whisper 25s，其余 30s）；
6. `train.py` 运行前 `torch.cuda.is_available()` 为真；
7. 训练前 `logs/44k` 内不存在本次任务未登记的 `G_*.pth`（避免误续训到别人的模型）；
8. 推理前模型 sha256 与登记一致；config 的 `speech_encoder` 与模型维度一致；
9. `cluster_infer_ratio > 0` 时，聚类/索引文件必须存在且可加载；
10. 端口：`train.port` 空闲（被占用则自动 +1 至可用）。

## A.8 与上游默认值的差异（必须实现，且需向用户解释）

| # | 项 | 上游 | 产品 | 原因 |
| --- | --- | --- | --- | --- |
| 1 | 训练 `batch_size` | 6 | 按显存（6GB→2） | 上游默认在 6GB 上大概率 OOM |
| 2 | 推理 `f0_predictor` | pm | 跟随训练记录（默认 rmvpe） | 训练/推理不一致会跑调 |
| 3 | 切片上限 | <30s（whisper） | 25s（whisper） | 留 5s 余量，规避边界崩 |
| 4 | 响度处理 | 峰值归一化到 0 dBFS | 峰值保护到 -1 dBFS | 0 dBFS 易削波，且 pyloudnorm 无压限 |
| 5 | 训练设备 | 全部 GPU | 单卡（默认） | 上游按卡数 spawn，多卡拆分 batch 并放大 OOM |
| 6 | 扩散 `batch_size` | 48 | 6GB→8 / 12GB→24 | 同上 |
| 7 | 预处理进程数 | 1 | 按显存 1/2/4 | 每进程占 GPU 显存 |

## A.9 输出命名契约

`results/{源名}_{key}_{spk}[_{ratio}]_{sovits|sovdiff|diff}_{f0p}.{flac|wav}`

- `key`：`auto`（自动音高）或 `{trans}key`；
- `ratio`：`cluster_infer_ratio > 0` 时出现；
- 产品保持该命名（用户可据此对照上游文档），额外写同名 `.json` 报告。

## A.10 外部模型（非本软件训练）兼容性契约

推理链路只依赖三样东西：`G_*.pth` 的 state_dict 形状、`config.json` 的结构字段、以及匹配的编码器/F0/声码器权重。因此"谁训的"不影响可行性。

### A.10.1 权重封装兼容

| 形态 | 处理 |
| --- | --- |
| 裸 `state_dict` | 直接使用 |
| `{"model": state_dict, "iteration": N, "optimizer": ...}`（上游 `save_checkpoint` 格式） | 取 `model` 字段 |
| 键带 `module.` 前缀（DDP 保存） | 逐键去前缀 |
| FP16 / BF16 存储 | 反序列化后 `float()` 再比对 |
| `release.pth`（`compress_model.py` 产物） | 缺 `enc_q.*`，判为 `ready(compressed)` |

### A.10.2 必需键（缺一即 `incompatible`）

`enc_p.*`、`flow.*`、`dec.*`、`emb_g.weight`；`config.model.speaker_embedding=true` 时还要 `ref_enc.*`。
**不必需**：`enc_q.*`（仅训练用）、`optimizer`/`learning_rate`/`iteration` 等训练态字段。

### A.10.3 形状核对口径

用 config 实例化 `SynthesizerTrn` 后，逐键比较 `state_dict[k].shape` 与 `model.state_dict()[k].shape`；**形状不一致按缺失处理**（比"键存在"更严格，能挡住"config 被改过"的模型）。

### A.10.4 需要自动补齐/推断的字段

| 情况 | 处理 | UI |
| --- | --- | --- |
| 4.0 模型缺 `model.speech_encoder` | 若 `ssl_dim==256` → 补 `vec256l9` | 标"推断"，要求用户确认 |
| 缺 `spk` 映射 | 用 `range(n_speakers)` 生成序号名 | 警告"说话人名称丢失，只能按序号选" |
| `emb_g.weight` 行数 ≠ `len(spk)` | 以权重行数为准建映射 | 警告"映射可能错位" |
| `vol_embedding` 与权重里是否有 `emb_vol` 不一致 | 以权重为准 | 标"按权重推断" |
| `data.sampling_rate != 44100` | 不推断，直接拒绝 | 阻断 |

### A.10.5 依赖资产（外部模型同样需要）

| config 字段 | 需要的资产 | 缺失后果 |
| --- | --- | --- |
| `speech_encoder ∈ {vec768l12, vec256l9}` | `pretrain/checkpoint_best_legacy_500.pt` | 该模型不可推理 |
| `hubertsoft` | `pretrain/hubert-soft-0d54a1f4.pt` | 同上 |
| `whisper-ppg` / `whisper-ppg-large` | `pretrain/medium.pt` / `large-v2.pt` | 同上，且强制 `clip=25`、`lg=1` |
| `cnhubertlarge` / `dphubert` / `wavlmbase+` | 对应权重 | 同上 |
| 推理选 `rmvpe`/`fcpe` | `pretrain/rmvpe.pt` / `fcpe.pt` | 换用 pm/dio/harvest 或补权重 |
| 开 `enhance` 或 `shallow_diffusion` | `pretrain/nsf_hifigan/*` | 该参数不可用 |

### A.10.6 输出目录契约（外部模型）

```
imports/<slug>/
├─ model/                    导入副本（默认复制；可"仅引用"）
│  ├─ G_xxx.pth  config.json  compat.json
│  └─ diffusion/  cluster/   可选
├─ inputs/<yyyyMMdd-HHmm>_<batch>/   源音频暂存（44.1k 单声道 wav）
└─ outputs/<yyyyMMdd-HHmm>_<batch>/  每次推理一个独立文件夹
   ├─ <源名>_<key>_<spk>...flac
   ├─ report.json            指标 + 参数快照 + 模型 sha256
   └─ params.json            可回放的参数
```

禁止写入：模型来源目录、项目 `results/`、安装目录 `app\ runtime\ assets\`。违规路径保存前拦截（SVC-6007）。

## A.11 上传/导入契约（拖拽即复制）

### A.11.1 文件类型识别

| 扩展名 | 语义 | 处理 |
| --- | --- | --- |
| `.pth` / `.pt` / `.ckpt` | 权重 | 按 A.10 判定；`.pt` 也用于扩散模型 |
| `.json` | config | 必须与权重 1:1；非 config 结构的 json 视为"识别失败"而非"缺配置" |
| `.pkl` | 聚类索引 / 特征检索索引 | 作为关联产物，失败不阻断主模型 |
| `.zip` / `.svcmodel.zip` | 模型包 | 只解压数据文件；含可执行文件直接拒绝 |
| 其他 | — | 忽略，结果里列"已跳过" |

**内容嗅探优先于扩展名**：`.pth` 必须能被 `torch.load(map_location="cpu")` 打开；打不开按"不是有效模型文件"处理，不按扩展名硬判。

### A.11.2 完整性前置（不通过就弹窗，不复制）

拖入后 ≤1s 内只读"文件名 / 大小 / JSON 头部"，**不读权重内容、不复制任何字节**：

| 条件 | 结论 |
| --- | --- |
| 权重恰好 1 个 且 config（`.json`）恰好 1 个 | 通过，进入复制 |
| 权重 0 个 / config 0 个 | 弹窗 A「请上传模型和配置文件」 |
| 权重 ≥2 或 config ≥2 | 同一弹窗，副文案"一次只能导入一个模型"；**不做批量导入** |
| zip 内无 `config.json` 且无 `manifest.json` 声明 | 同一弹窗，不解包 |
| 其他文件 | 忽略并列出 |

**不做配对猜测，也没有手动配对面板**：1:1 之外一律按"缺件"处理。

### A.11.3 流程（七步，任一步失败可重试且源文件不受影响）

```
① 完整性：1 权重 + 1 config？（不通过→弹窗A，结束，零磁盘占用）
② 预检：总字节、目标盘余量 ≥ 总量×1.1 + 2GB、扩展名/文件数上限
③ 复制：源 → models/.staging/<uuid>/，边复制边算 sha256（64KB 分块）
④ 复算：对 staging 文件重算 sha256 并与 ③ 比对
⑤ 落位：os.replace 到 models/imports/<slug>/model/
⑥ 识别：A.10 的 7 步兼容性判定（不通过→弹窗B，目录改名 _unrecognized/）
⑦ 登记：写 models / import_sessions / job_events
```

- staging 目录：`models/.staging/`，超过 24h 或任务取消即清理；`_unrecognized/` 保留 7 天。
- 单文件 ≤ 8GB；单次 1 个权重 + 1 个 config；递归扫描深度 ≤ 3。
- 进度以"已复制字节 / 总字节"上报，UI 显示 MB/s 与剩余时间。

### A.11.4 落位命名

`models/imports/<slug>/model/`，`slug = <模型名>-<sha256 前 8 位>`，模型名取文件名 stem 或包内 `manifest.name`；重名但 sha256 不同则追加 `-2`、`-3`。

### A.11.5 失败语义

| 阶段 | 失败 | 错误码 | 现场保留 / UI |
| --- | --- | --- | --- |
| 完整性 | 缺模型或缺配置、数量非 1:1 | SVC-6008 | 不复制、无 staging；弹窗 A「请上传模型和配置文件」 |
| 预检 | 磁盘不足 | SVC-1006 | 无；弹错误并给"换目录" |
| 复制 | 读写错误 / U 盘拔出 | SVC-2004 | staging 保留，可重试 |
| 复算 | sha256 不一致 | SVC-2004 | staging 保留，可重试 |
| 识别 | 结构不兼容 / config 非法 / 非 44.1k | SVC-6005 | 移到 `_unrecognized/<slug>/` 保留 7 天；弹窗 B「无法识别该模型」 |
| 依赖 | 缺编码器/F0/声码器权重 | SVC-6006 | 已落位，模型卡 `needs_attention`，**不弹窗** |
| 安全 | 未勾选"来源可信" | SVC-6009 | 不启动导入（零磁盘），上传区显示提示条 |
| 包安全 | zip 内含 `.exe/.bat/.ps1` | SVC-6010 | 不解包、不导入，列出可疑条目 |
