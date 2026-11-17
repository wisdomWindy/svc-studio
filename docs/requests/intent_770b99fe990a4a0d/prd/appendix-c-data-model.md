# 附录 C：数据模型

> 存储分工：**SQLite 存索引与状态**（可查询、可对账），**文件系统存大产物**（音频、权重、日志）。
> 原则：SQLite 里出现的每个路径都必须能在磁盘上找到；找不到即触发"产物缺失"错误（SVC-4003/7003）。

## C.1 SQLite 表

数据库位置：`<数据根>\studio.db`（默认 `%USERPROFILE%\SVCStudio\studio.db`；WAL 模式，`synchronous=NORMAL`，`foreign_keys=ON`）。

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `projects` | `id, name, slug, root_path, speaker, speech_encoder, f0_predictor, material_type(singing|speech), gpu_index, status, created_at, updated_at` | 一个项目 = 一个工作区 |
| `datasets` | `id, project_id, version, root_path, file_count, total_seconds, fingerprint, low_data, qc_path, created_at` | 数据集版本（重导入生成新版本） |
| `dataset_files` | `id, dataset_id, rel_path, seconds, sample_rate, channels, sha256, status(ok|quarantine|excluded), issues_json` | 单文件 QC 结果 |
| `qc_reports` | `id, dataset_id, json_path, passed, blocking_count, warning_count, generated_at` | 报告落盘 + 索引 |
| `preprocess_runs` | `id, project_id, dataset_id, speech_encoder, f0_predictor, use_diff, vol_aug, num_processes, status, manifest_path, started_at, finished_at` | 一次三步链路 |
| `artifacts` | `id, run_id, kind(wav|soft|f0|spec|vol|mel|aug_mel|aug_vol), rel_path, sha256, bytes, counted` | 产物登记，用于计数校验 |
| `train_runs` | `id, project_id, kind(main|diffusion|cluster|index), config_path, params_json, params_hash, status, start_step, end_step, total_steps, peak_vram_mb, report_path, job_id` | 训练/扩散/聚类/索引 |
| `checkpoints` | `id, train_run_id, step, path, sha256, bytes, created_at, is_default` | checkpoint 清单 |
| `models` | `id, project_id, name, step, path, sha256, bytes, speech_encoder, f0_predictor, vocoder, vol_embedding, config_path, config_sha256, dataset_fingerprint, train_run_id, diffusion_path, cluster_path, index_path, source(trained|imported|mixed), origin_dir, copied(0|1), compat_json, required_assets_json, status(ready|compressed|incompatible|missing), mixed, created_at` | 全局模型库；`project_id` 只记来源，不限制被引用；`imported` 的模型文件落在 `imports/<slug>/model/` |
| `import_sessions` | `id, job_id, source_kind(files|folder|zip), source_paths_json, staging_dir, bytes_total, bytes_done, file_count, status(incomplete|copying|verifying|recognizing|decided|rejected|failed|cancelled), error_code, model_id, created_at` | 拖拽上传会话；`incomplete` 只记事件不落磁盘（弹窗 A），`rejected` 对应 `_unrecognized/`（弹窗 B） |
| `inference_runs` | `id, project_id, model_id, batch_name, model_scope(project|other|manual|imported), params_json, params_hash, input_count, ok_count, fail_count, status, report_path, job_id` | 一次单条/批量 = 一个批次（`results/<批次>/`） |
| `inference_items` | `id, run_id, input_path, input_sha256, output_path, status, error_code, rtf, peak_dbfs, clip_ratio, seconds_in, seconds_out` | 批量中的单条 |
| `presets` | `id, name, scope(inference|train), params_json, builtin, created_at` | 内置 + 用户预设 |
| `jobs` | `id, type, project_id, payload_json, status, progress, step, total, error_code, pid, started_at, finished_at, parent_job_id` | 任务中心主表 |
| `job_events` | `id, job_id, seq, ts, level, code, message, data_json` | append-only 事件流 |
| `assets` | `id, kind(encoder|f0|vocoder|pretrained|diffusion), key, version, status, bytes, sha256, local_path, source_url, verified_at` | 全局资产 |
| `asset_files` | `id, asset_id, rel_path, bytes, sha256, status` | 多文件资产（如 nsf_hifigan 4 文件） |
| `consents` | `id, kind(agpl|usage|dataset|voice), doc_version, app_version, subject, accepted_at, hash` | 合规留痕 |
| `settings` | `key, value_json, updated_at` | 路径、主题、GPU、镜像 |

**索引**：`jobs(project_id, status)`、`job_events(job_id, seq)`、`dataset_files(dataset_id, status)`、`models(project_id, status)`、`inference_items(run_id, status)`。

## C.2 状态与约束

- `jobs.status ∈ {blocked, queued, running, stopping, stopped, succeeded, failed, cancelled, interrupted}`；`stopped` 仅对 `train/diffusion` 合法。
- `models.status = missing` 表示磁盘上的文件不见了（启动对账时置位），UI 标灰并给"重新定位"。
- `datasets.fingerprint` 唯一性不强制（允许同数据重新导入生成 v2，但需提示"与 v1 相同"）。
- 外键删除策略：`projects` 删除 = 级联删除其 datasets/train_runs/models/jobs 记录，**但磁盘文件需用户确认**（默认保留，标记为 `orphan`）。

## C.3 文件与原子写

1. 所有写操作：`<target>.tmp.<pid>` → `fsync` → `os.replace` 到目标；跨盘移动用"复制到临时 + 校验 + 删除源"。
2. 大文件写入期间在 `.svcstudio/tmp/` 占位，任务失败即清理；`.tmp` 超过 24h 自动清理。
3. 配置修改前备份：`configs/.history/config-<ISO8601>.json`，保留最近 20 份。
4. 日志：`logs/jobs/<job_id>.log`（原始 stdout/stderr）+ `job_events`（结构化）；原始日志超过 20MB 滚动。

**数据根（默认 `%USERPROFILE%\SVCStudio`，可整体迁移 / 便携模式随应用走）**

```
<数据根>\
├─ studio.db                 SQLite（WAL）
├─ projects\<slug>\          项目工作区（见 PRD §5.1）
├─ models\
│  ├─ .staging\<uuid>\       上传中转（复制/复算/判定期间）；24h 或取消即清
│  └─ imports\<slug>\        外部模型：model\ inputs\ outputs\<批次>\
├─ assets\pretrain\          编码器/F0/声码器/底模
├─ logs\jobs\                任务原始日志
└─ tmp\                      原子写中转
```

- `.staging` 是唯一允许"半成品"的目录；`imports\` 下出现半成品视为异常，启动对账时清理并记 `SVC-2004`。
- `imports\<slug>\` 只存外部导入模型，与项目完全隔离；删项目不动 `imports\`，删模型不动已导出的 `outputs\<批次>\`。

## C.4 关键 JSON 结构

### C.4.1 `reports/qc_report.json`

```json
{
  "schema": "svcstudio.qc/1",
  "dataset_id": 12,
  "generated_at": "2026-11-16T20:00:00+08:00",
  "summary": {"file_count": 842, "total_seconds": 1923.4, "total_minutes": 32.06,
              "low_data": false, "passed": true, "blocking": 0, "warnings": 2},
  "metrics": {
    "duration": {"min": 2.1, "p50": 8.4, "p95": 14.2, "max": 15.0},
    "sample_rate": {"44100": 840, "48000": 2},
    "channels": {"1": 842},
    "silence_ratio": 0.061,
    "clipping_ratio": 0.0002,
    "loudness_lufs": {"mean": -14.8, "p10": -19.2, "p90": -11.4}
  },
  "blocking_items": [],
  "warnings": [{"code": "SVC-3003", "count": 2, "sample": "dataset_raw/小满/测试 1.wav"}]
}
```

### C.4.2 `preprocess_manifest.json`

```json
{
  "schema": "svcstudio.preprocess/1",
  "run_id": 7,
  "steps": [
    {"name": "resample", "cmd": ["python", "resample.py", "--skip_loudnorm"],
     "cwd": "<project>", "started_at": "...", "finished_at": "...", "exit_code": 0},
    {"name": "flist_config", "cmd": ["python", "preprocess_flist_config.py", "--speech_encoder", "vec768l12"],
     "exit_code": 0},
    {"name": "hubert_f0", "cmd": ["python", "preprocess_hubert_f0.py", "--f0_predictor", "rmvpe",
                                  "--num_processes", "1"], "exit_code": 0}
  ],
  "counts": {"wav": 842, "soft": 842, "f0": 842, "spec": 842, "vol": 0, "mel": 0},
  "diffs": [],
  "configs": {"config_sha256": "...", "diffusion_sha256": "...",
              "min_clip_seconds": 2.1, "diffusion_duration": 2.0}
}
```

### C.4.3 `reports/train_report.json`

```json
{
  "schema": "svcstudio.train/1",
  "run_id": 31, "job_id": "job_01H...", "kind": "main",
  "model_step": 2400, "total_steps": 100000,
  "started_at": "...", "finished_at": "...", "elapsed_seconds": 7212,
  "steps_per_second": 0.333, "peak_vram_mb": 5720,
  "loss": {"g_total": {"first": 12.4, "last": 4.1, "min": 3.8},
            "d_total": {"first": 2.9, "last": 1.7, "min": 1.5},
            "nan_detected": false},
  "auto_downgrade": [{"from_batch": 6, "to_batch": 3, "reason": "SVC-5001"}],
  "params_snapshot": {"batch_size": 3, "epochs": 100000, "lr": 0.0001, "fp16": true,
                      "keep_ckpts": 3, "all_in_mem": false, "port": 8002},
  "dataset_fingerprint": "sha256:...", "low_data": false,
  "checkpoints": [{"step": 800, "bytes": 123456789, "sha256": "..."}]
}
```

### C.4.4 `reports/inference_report.json`

```json
{
  "schema": "svcstudio.infer/1",
  "run_id": 44, "model_sha256": "...", "model_step": 2400,
  "params": {"f0_predictor": "rmvpe", "trans": 0, "auto_predict_f0": false, "clip": 0,
             "lg": 0, "cluster_ratio": 0.0, "shallow_diffusion": false, "k_step": 100,
             "enhance": false, "loudness_envelope_adjustment": 1.0, "spk": "xiaoman"},
  "params_hash": "sha256:...",
  "items": [{"input": "raw/song.wav", "output": "results/song_0key_xiaoman_sovits_rmvpe.flac",
             "seconds_in": 213.4, "seconds_out": 213.5, "rtf": 1.12,
             "peak_dbfs": -0.4, "clip_ratio": 0.0, "status": "ok"}],
  "summary": {"ok": 1, "failed": 0}
}
```

### C.4.5 导出音频元数据（FLAC Vorbis comment / WAV LIST-INFO / 同名 `.json`）

```
SVCSTUDIO_MODEL_SHA256=...
SVCSTUDIO_MODEL_STEP=2400
SVCSTUDIO_CONFIG_SHA256=...
SVCSTUDIO_PARAMS_HASH=...
SVCSTUDIO_DATASET_FINGERPRINT=sha256:...
SVCSTUDIO_APP_VERSION=1.0.0
SVCSTUDIO_CREATED_AT=2026-11-16T20:00:00+08:00
SVCSTUDIO_SOURCE_HINT=（发布时请注明输入源）
```

## C.5 迁移与恢复

- `PRAGMA user_version` 记录 schema 版本；启动时按版本顺序执行迁移脚本，迁移前自动备份 `studio.db` 为 `studio.db.bak.<version>`。
- 数据库打不开 → 尝试移除 `-wal/-shm` 后重试；仍失败则把损坏文件改名保留，并引导从最近备份恢复（SVC-7003）。
- 启动对账：`jobs.status=running` 但 pid 不存在 → `interrupted`；`models.path` 不存在 → `missing`。
