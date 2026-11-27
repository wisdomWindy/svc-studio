//! 数据集命令：扫描、QC（含落库与报告落盘）。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{Emitter, State};

use crate::core::ids::{dataset_id, event_id};
use crate::core::models::DatasetSummary;
use crate::core::repo::{datasets, projects};
use crate::core::worker_cli::run_json;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::infra::fs::{atomic_write, sha256_file};
use crate::AppPaths;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanIssue {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QcFile {
    pub rel_path: String,
    pub seconds: f64,
    pub sample_rate: i64,
    pub channels: i64,
    pub silence_ratio: f64,
    pub clipping_ratio: f64,
    pub rms_dbfs: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QcAssertions {
    pub passed: bool,
    pub blocking: Vec<ScanIssue>,
    pub warnings: Vec<ScanIssue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QcSummary {
    pub file_count: i64,
    pub total_seconds: f64,
    pub total_minutes: f64,
    pub low_data: bool,
    pub passed: bool,
    pub blocking: i64,
    pub warnings: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QcReport {
    pub schema: String,
    pub generated_at: String,
    pub summary: QcSummary,
    /// 指标是 worker 产出的动态结构，直接透传给前端，避免再定义一套同构类型。
    #[serde(default)]
    pub metrics: Option<serde_json::Value>,
    #[serde(default)]
    pub scan: Option<serde_json::Value>,
    pub files: Vec<QcFile>,
    pub assertions: QcAssertions,
}

fn project_root(state: &AppPaths, project_id: &str) -> AppResult<PathBuf> {
    let project = state
        .db
        .with(|c| projects::get(c, project_id))?
        .ok_or_else(|| {
            Box::new(AppError::new(
                "SVC-7004",
                "项目不存在",
                project_id.to_string(),
            ))
        })?;
    Ok(PathBuf::from(project.root_path))
}

fn dataset_raw(root: &Path) -> PathBuf {
    root.join("dataset_raw")
}

#[tauri::command]
pub async fn dataset_scan(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<serde_json::Value> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let dir = dataset_raw(&root);
    // 不设超时：扫描耗时随文件数增长，慢盘/大库上固定上限会误杀
    crate::core::worker_cli::run_json(
        &python,
        &["dataset", "scan", "--root", &dir.to_string_lossy()],
    )
    .await
}

/// 导入后立刻可调的带宽体检：估算这批素材能支撑的模型输出亮度上限。
///
/// 只读、很快（逐文件解码一次），所以不放进耗时的 prepare 链条里——
/// 用户导入完马上就能看到「素材偏闷 / 良好」的判断，不必等分离跑完。
#[tauri::command]
pub async fn dataset_bandwidth(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<serde_json::Value> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let dir = dataset_raw(&root);
    if !dir.is_dir() {
        return Ok(serde_json::json!({
            "schema": "svcstudio.dataset.bandwidth/1",
            "files": 0,
            "buckets": {},
            "verdict": { "level": "none", "label": "无素材", "advice": "先导入音频文件" },
        }));
    }
    crate::core::worker_cli::run_json(
        &python,
        &["dataset", "bandwidth", "--root", &dir.to_string_lossy()],
    )
    .await
}

/// QC 一次 = 数据集的一个版本；文件清单、报告、断言结果都落库。
#[tauri::command]
pub fn dataset_qc(
    app: tauri::AppHandle,
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<()> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let dir = dataset_raw(&root);
    if !dir.is_dir() {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "还没有可质检的数据集",
            format!("{} 不存在", dir.display()),
        )));
    }

    let report_path = root.join("reports").join("qc_report.json");
    let db = state.db.clone();
    tauri::async_runtime::spawn(async move {
        let handle = app.clone();
        if let Err(err) = qc_task(handle.clone(), db, python, dir, report_path, project_id).await {
            handle
                .emit(
                    "dataset-progress",
                    serde_json::json!({
                        "kind": "qc",
                        "stage": "qc-error",
                        "code": err.code,
                        "message": err.message,
                        "cause": err.cause,
                    }),
                )
                .ok();
        }
    });
    Ok(())
}

/// 真正的 QC：跑 worker（--events 流式进度）→ 落库 → 返回报告。
async fn qc_task(
    app: tauri::AppHandle,
    db: std::sync::Arc<crate::infra::db::Db>,
    python: std::path::PathBuf,
    dir: std::path::PathBuf,
    report_path: std::path::PathBuf,
    project_id: String,
) -> AppResult<()> {
    let dir_text = dir.to_string_lossy().to_string();
    let report_text = report_path.to_string_lossy().to_string();

    let mut cmd = tokio::process::Command::new(&python);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args([
        "dataset",
        "qc",
        "--root",
        dir_text.as_str(),
        "--out",
        report_text.as_str(),
        "--events",
    ]);
    for (k, v) in crate::core::runtime::worker_env(&crate::core::runtime::resolve_worker_dir()) {
        cmd.env(k, v);
    }
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker",
            format!("{}: {e}", python.display()),
        ))
    })?;

    let mut report_value: Option<serde_json::Value> = None;
    if let Some(stdout) = child.stdout.take() {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(value) = serde_json::from_str::<serde_json::Value>(line.trim()) else {
                continue;
            };
            match value.get("kind").and_then(|k| k.as_str()) {
                Some("progress") => {
                    app.emit(
                        "dataset-progress",
                        serde_json::json!({
                            "kind": "qc",
                            "stage": "qc",
                            "step": value.get("step").cloned().unwrap_or_default(),
                            "total": value.get("total").cloned().unwrap_or_default(),
                        }),
                    )
                    .ok();
                }
                Some("result") => {
                    report_value = value.get("data").cloned();
                }
                _ => {}
            }
        }
    }
    let status = child.wait().await.ok();
    if report_value.is_none() {
        return Err(Box::new(AppError::new(
            "SVC-3001",
            "质检没有返回结果",
            format!("worker 退出码 {:?}", status.and_then(|s| s.code())),
        )));
    }

    let report: QcReport = serde_json::from_value(report_value.unwrap()).map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "QC 报告结构不符合预期",
            e.to_string(),
        ))
    })?;

    let ds_id = dataset_id();
    let now = now_iso();
    let report_for_db = report.clone();
    let dir_for_db = dir.clone();
    let report_path_for_db = report_path.clone();
    let summary: DatasetSummary = db.with(|c| {
        let ds = datasets::insert(c, &ds_id, &project_id, &dir_for_db.to_string_lossy(), &now)?;
        for file in &report_for_db.files {
            let abs = dir_for_db.join(&file.rel_path);
            let sha = if abs.is_file() {
                sha256_file(&abs).ok()
            } else {
                None
            };
            datasets::insert_file(
                c,
                &datasets::NewDatasetFile {
                    id: &event_id(),
                    dataset_id: &ds_id,
                    rel_path: &file.rel_path,
                    seconds: file.seconds,
                    sample_rate: file.sample_rate,
                    channels: file.channels,
                    sha256: sha.as_deref(),
                },
            )?;
        }
        let fingerprint = fingerprint_of(&report_for_db.files);
        datasets::finalize(
            c,
            &ds_id,
            report_for_db.summary.file_count,
            report_for_db.summary.total_seconds,
            &fingerprint,
            report_for_db.summary.low_data,
        )?;
        c.execute(
            "INSERT INTO qc_reports (id,dataset_id,json_path,passed,blocking_count,warning_count,generated_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7)",
            rusqlite::params![
                event_id(),
                ds_id,
                report_path_for_db.to_string_lossy(),
                i64::from(report_for_db.summary.passed),
                report_for_db.summary.blocking,
                report_for_db.summary.warnings,
                now
            ],
        )?;
        Ok(ds)
    })?;

    let serialized = serde_json::to_vec_pretty(&report)
        .map_err(|e| Box::new(AppError::new("SVC-7001", "报告序列化失败", e.to_string())))?;
    atomic_write(&report_path, &serialized)?;

    tracing::info!(dataset = %summary.id, files = summary.file_count, "qc done");
    app.emit(
        "dataset-progress",
        serde_json::json!({ "kind": "qc", "stage": "qc-done", "report": report }),
    )
    .ok();
    Ok(())
}

#[tauri::command]
pub async fn dataset_slice(
    state: State<'_, AppPaths>,
    project_id: String,
    min_seconds: Option<f64>,
    max_seconds: Option<f64>,
    min_interval_ms: Option<i64>,
) -> AppResult<serde_json::Value> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let dir = dataset_raw(&root);
    let min_s = min_seconds.unwrap_or(5.0);
    let max_s = max_seconds.unwrap_or(15.0);
    let interval = min_interval_ms.unwrap_or(300);
    run_json(
        &python,
        &[
            "dataset",
            "slice",
            "--root",
            &dir.to_string_lossy(),
            "--min-seconds",
            &min_s.to_string(),
            "--max-seconds",
            &max_s.to_string(),
            "--min-interval-ms",
            &interval.to_string(),
        ],
    )
    .await
}

#[tauri::command]
pub async fn dataset_resample(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<serde_json::Value> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let dir = dataset_raw(&root);
    run_json(
        &python,
        &[
            "dataset",
            "resample",
            "--root",
            &dir.to_string_lossy(),
            "--sample-rate",
            "44100",
        ],
    )
    .await
}

/// 数据集指纹：只依赖内容与结构，不依赖时间。
fn fingerprint_of(files: &[QcFile]) -> String {
    let mut parts: Vec<String> = files
        .iter()
        .map(|f| {
            format!(
                "{}:{:.3}:{}:{}",
                f.rel_path, f.seconds, f.sample_rate, f.channels
            )
        })
        .collect();
    parts.sort();
    crate::infra::fs::sha256_bytes(parts.join("\n").as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::infra::fs::sha256_bytes;

    fn sample_report_json() -> serde_json::Value {
        serde_json::json!({
            "schema": "svcstudio.qc/1",
            "generatedAt": "2026-01-01T00:00:00Z",
            "datasetRoot": "C:/p/dataset_raw",
            "summary": {"fileCount": 1, "totalSeconds": 8.0, "totalMinutes": 0.13,
                        "lowData": true, "passed": true, "blocking": 0, "warnings": 1},
            "metrics": {"duration": {"min": 8.0, "p50": 8.0, "p95": 8.0, "max": 8.0}},
            "files": [{"relPath": "spk/a.wav", "seconds": 8.0, "sampleRate": 44100,
                       "channels": 1, "silenceRatio": 0.01, "clippingRatio": 0.0,
                       "rmsDbfs": -18.2}],
            "scan": {"nonWav": [], "emptyDirs": [], "nested": []},
            "assertions": {"passed": true, "blocking": [], "warnings": []}
        })
    }

    #[test]
    fn qc_report_deserializes_from_python_shape() {
        let report: QcReport = serde_json::from_value(sample_report_json()).expect("parse");
        assert_eq!(report.files.len(), 1);
        assert!(report.assertions.passed);
        assert_eq!(report.summary.file_count, 1);
    }

    #[test]
    fn fingerprint_is_order_independent_and_content_sensitive() {
        let a = QcFile {
            rel_path: "spk/a.wav".into(),
            seconds: 8.0,
            sample_rate: 44100,
            channels: 1,
            silence_ratio: 0.0,
            clipping_ratio: 0.0,
            rms_dbfs: -18.0,
        };
        let b = QcFile {
            rel_path: "spk/b.wav".into(),
            ..a.clone()
        };
        assert_eq!(
            fingerprint_of(&[a.clone(), b.clone()]),
            fingerprint_of(&[b.clone(), a.clone()])
        );
        let mut c = b.clone();
        c.seconds = 9.0;
        assert_ne!(fingerprint_of(&[b]), fingerprint_of(&[c]));
        assert_eq!(fingerprint_of(&[]), sha256_bytes(b""));
    }

    fn write_wav_1s(path: &Path) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        let sr: u32 = 44_100;
        let samples: Vec<i16> = (0..sr)
            .map(|i| if (i / 100) % 2 == 0 { 8_000 } else { -8_000 })
            .collect();
        let data_len = (samples.len() * 2) as u32;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16u32.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&sr.to_le_bytes());
        bytes.extend_from_slice(&(sr * 2).to_le_bytes());
        bytes.extend_from_slice(&2u16.to_le_bytes());
        bytes.extend_from_slice(&16u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_len.to_le_bytes());
        for s in &samples {
            bytes.extend_from_slice(&s.to_le_bytes());
        }
        std::fs::write(path, bytes).unwrap();
    }

    #[tokio::test]
    async fn scan_runs_against_real_worker() {
        let worker = crate::core::runtime::resolve_worker_dir();
        if !worker.join("svc_bridge").is_dir() {
            eprintln!("skip: worker 源码不在预期位置");
            return;
        }
        let base = std::env::temp_dir().join(format!("svcscan-{}", uuid::Uuid::now_v7().simple()));
        let dir = base.join("dataset_raw");
        write_wav_1s(&dir.join("spk").join("a.wav"));

        let root = dir.to_string_lossy().to_string();
        let value = crate::core::worker_cli::run_json(
            &crate::core::runtime::resolve_python(),
            &["dataset", "scan", "--root", &root],
        )
        .await
        .expect("scan 必须返回结构化结果");

        assert_eq!(value["speakers"].as_array().unwrap().len(), 1);
        assert_eq!(value["files"].as_array().unwrap().len(), 1);
        let seconds = value["files"][0]["seconds"].as_f64().unwrap();
        assert!((seconds - 1.0).abs() < 0.05, "时长 {seconds}");
        let _ = std::fs::remove_dir_all(base);
    }
}

/// 把用户从资源管理器选中的文件/目录复制进项目的受管目录。
#[tauri::command]
pub async fn dataset_import(
    state: State<'_, AppPaths>,
    project_id: String,
    paths: Vec<String>,
) -> AppResult<serde_json::Value> {
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;
    let raw = dataset_raw(Path::new(&project.root_path));
    let target = crate::commands::dataset_import::speaker_dir(&raw, project.speaker.as_deref());

    let input: Vec<std::path::PathBuf> = paths.into_iter().map(std::path::PathBuf::from).collect();
    if input.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-4002",
            "没有选择任何文件",
            "请选择音频文件或包含音频的文件夹",
        )));
    }

    let (sources, rejected) = crate::commands::dataset_import::collect_sources(&input);
    if sources.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-4002",
            "没有可导入的音频",
            format!("{rejected} 个文件扩展名不受支持（支持 wav/flac/mp3/m4a/aac/ogg/opus/wma）"),
        )));
    }

    let mut outcome = crate::commands::dataset_import::import_files(&sources, &target)?;
    outcome.rejected = rejected;
    Ok(serde_json::to_value(outcome).unwrap_or(serde_json::Value::Null))
}

/// 启动数据集准备任务并立即返回；进度与结果通过 `dataset-progress` 事件回传。
///
/// 流程：ffmpeg 转 44.1k 单声道 → 超过 15s 切 5–15s → 统一下响度。
/// 分离（UVR5 MDX23C，失败回退 Demucs）+ 切片可能跑几分钟，远超 IPC 的 15 秒超时，所以不在命令里等待。
#[tauri::command]
pub fn dataset_prepare(
    app: tauri::AppHandle,
    state: State<'_, AppPaths>,
    project_id: String,
    separate: Option<bool>,
) -> AppResult<()> {
    use tauri::Emitter;

    let handle = app.clone();
    let separate = separate.unwrap_or(true);
    // 先确认 python 可用，避免异步启动后才发现环境问题
    let python = crate::commands::settings::python_for_state(&state)?;
    let root = project_root(&state, &project_id)?;
    let handle2 = handle.clone();
    tauri::async_runtime::spawn(async move {
        let progress = |stage: &str, message: &str| {
            handle2
                .emit(
                    "dataset-progress",
                    serde_json::json!({ "stage": stage, "message": message }),
                )
                .ok();
        };
        progress(
            "separate",
            if separate {
                "分离人声…"
            } else {
                "跳过分离"
            },
        );
        let result = run_prepare(python, root, separate, &handle2).await;
        match result {
            Ok(value) => {
                handle
                    .emit(
                        "dataset-progress",
                        serde_json::json!({ "stage": "done", "result": value }),
                    )
                    .ok();
            }
            Err(e) => {
                handle
                    .emit(
                        "dataset-progress",
                        serde_json::json!({
                            "stage": "error",
                            "code": e.code,
                            "message": e.message,
                            "cause": e.cause,
                        }),
                    )
                    .ok();
            }
        }
    });
    Ok(())
}

/// 跑带 `--events` 的 worker 命令，把逐文件进度转发成 `dataset-progress` 事件。
///
/// 分离一首歌要十几秒，界面必须能看到「已分离 N/M」而不是一句静态文案。
/// 结果仍从 `{"kind":"result","data":…}` 事件取，stderr 丢弃（torch/librosa 警告很多，
/// 管道写满会把子进程卡死）。
async fn run_streaming(
    python: &std::path::Path,
    args: &[&str],
    app: &tauri::AppHandle,
    stage_name: &str,
    label: &str,
) -> AppResult<serde_json::Value> {
    use tauri::Emitter;

    let mut cmd = tokio::process::Command::new(python);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args(args);
    cmd.arg("--events");
    for (k, v) in crate::core::runtime::worker_env(&crate::core::runtime::resolve_worker_dir()) {
        cmd.env(k, v);
    }
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::null());

    let mut child = cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker",
            format!("{}: {e}", python.display()),
        ))
    })?;

    let mut result: Option<serde_json::Value> = None;
    let mut failure: Option<String> = None;
    if let Some(stdout) = child.stdout.take() {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(value) = serde_json::from_str::<serde_json::Value>(line.trim()) else {
                continue;
            };
            match value.get("kind").and_then(|k| k.as_str()) {
                Some("progress") => {
                    let step = value.get("step").and_then(|v| v.as_u64()).unwrap_or(0);
                    let total = value.get("total").and_then(|v| v.as_u64()).unwrap_or(0);
                    app.emit(
                        "dataset-progress",
                        serde_json::json!({
                            "kind": "prepare",
                            "stage": stage_name,
                            "step": step,
                            "total": total,
                            "message": if total > 0 {
                                format!("{label} {step}/{total}")
                            } else {
                                format!("{label}…")
                            },
                        }),
                    )
                    .ok();
                }
                Some("result") => result = value.get("data").cloned(),
                Some("error") => {
                    failure = Some(format!(
                        "[{}] {}",
                        value
                            .get("code")
                            .and_then(|c| c.as_str())
                            .unwrap_or("SVC-3001"),
                        value
                            .get("message")
                            .and_then(|m| m.as_str())
                            .unwrap_or("worker 报告错误")
                    ))
                }
                _ => {}
            }
        }
    }
    let _ = child.wait().await;

    if let Some(err) = failure {
        return Err(Box::new(AppError::new("SVC-3001", "分离失败", err)));
    }
    result.ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-3001",
            "worker 没有返回结果",
            format!("{stage_name} 结束但没有 result 事件"),
        ))
    })
}

async fn run_prepare(
    python: std::path::PathBuf,
    root: std::path::PathBuf,
    separate: bool,
    app: &tauri::AppHandle,
) -> AppResult<serde_json::Value> {
    use tauri::Emitter;
    let dir = dataset_raw(&root);
    let root_arg = dir.to_string_lossy().to_string();
    let stage = |name: &str, message: &str| {
        app.emit(
            "dataset-progress",
            serde_json::json!({ "stage": name, "message": message }),
        )
        .ok();
    };

    // 源文件带宽必须在分离前测：finalize 之后原文件会被清理，
    // 而「干声带宽」取决于分离器残留（同源 RoFormer 11kHz / Demucs 22kHz），不能当素材上限
    stage("bandwidth", "检测素材带宽…");
    let source_bandwidth = run_json(&python, &["dataset", "bandwidth", "--root", &root_arg])
        .await
        .unwrap_or(serde_json::Value::Null);

    let mut sep_report = serde_json::Value::Null;
    if separate {
        stage("separate", "分离人声（UVR5 MDX23C）…");
        // 流式跑：逐文件回传「已分离 N/M」，不再是一句静态文案
        sep_report = run_streaming(
            &python,
            &["dataset", "separate", "--root", &root_arg],
            app,
            "separate",
            "已分离",
        )
        .await?;
        let sep_failed = sep_report
            .get("failed")
            .and_then(|v| v.as_array())
            .map(|v| v.len())
            .unwrap_or(0);
        if sep_failed > 0 {
            return Err(Box::new(AppError::new(
                "SVC-3001",
                "人声分离失败",
                format!("{sep_failed} 个文件分离失败"),
            )));
        }
    }

    stage("convert", "转换格式…");
    let converted = run_streaming(
        &python,
        &["dataset", "convert", "--root", &root_arg],
        app,
        "convert",
        "已转码",
    )
    .await?;
    stage("slice", "切片…");
    let sliced = run_streaming(
        &python,
        &["dataset", "slice", "--root", &root_arg],
        app,
        "slice",
        "已切片",
    )
    .await?;
    stage("resample", "统一 44.1k 单声道…");
    let resampled = run_streaming(
        &python,
        &["dataset", "resample", "--root", &root_arg],
        app,
        "resample",
        "已重采样",
    )
    .await?;
    // 切片后精修：只做 Layer-0（时长/削波/近重复）+ 响度归一化。
    // 带宽与 SNR 代理不能在这里当删除判据：
    // - 干声带宽取决于分离器残留（同一源：RoFormer 干声 11kHz、Demucs 22kHz），
    //   测的是分离噪声底而不是素材上限；
    // - quietFrameRatio 在 20dB 以上饱和，不是 SNR。
    // 两者都只在报告里出现，需要时可用 --min-rolloff-hz / --min-snr-db 显式开启。
    stage("refine", "按质量阈值精修切片…");
    let refined = run_streaming(
        &python,
        &["dataset", "refine", "--root", &root_arg],
        app,
        "refine",
        "已精修",
    )
    .await?;
    stage("finalize", "清理中间产物…");
    let finalized = run_streaming(
        &python,
        &["dataset", "finalize", "--root", &root_arg],
        app,
        "finalize",
        "已清理",
    )
    .await?;

    let failed = converted
        .get("failed")
        .and_then(|v| v.as_array())
        .map(|v| v.len())
        .unwrap_or(0);
    if failed > 0 {
        return Err(Box::new(AppError::new(
            "SVC-3001",
            "部分音频无法转换",
            format!(
                "{failed} 个文件解码失败：{}",
                converted
                    .get("failed")
                    .map(|v| v.to_string())
                    .unwrap_or_default()
            ),
        )));
    }

    Ok(serde_json::json!({
        "separated": sep_report.get("separated").cloned().unwrap_or(serde_json::Value::Null),
        "separatedSeconds": sep_report.get("seconds").cloned().unwrap_or(serde_json::Value::Null),
        // 分离前体检跳过的源文件（解码失败/削波/长静音/时长异常），带原因
        "skippedSources": sep_report.get("skipped").cloned().unwrap_or(serde_json::Value::Null),
        "converted": converted.get("converted").cloned().unwrap_or(serde_json::Value::Null),
        "skipped": converted.get("skipped").cloned().unwrap_or(serde_json::Value::Null),
        "sliced": sliced.get("sliced").cloned().unwrap_or(serde_json::Value::Null),
        "resampled": resampled.get("changed").cloned().unwrap_or(serde_json::Value::Null),
        // 分离质量（只报告：伴奏泄漏相关 + 人声占比）
        "separationQuality": sep_report.get("quality").cloned().unwrap_or(serde_json::Value::Null),
        // 源文件带宽（−50dB 上限口径），与界面上的「素材带宽体检」同源
        "sourceBandwidth": source_bandwidth,
        "refinedKept": refined.get("keptFiles").cloned().unwrap_or(serde_json::Value::Null),
        "refinedRejected": refined
            .get("rejectedFiles")
            .cloned()
            .unwrap_or(serde_json::Value::Null),
        "refinedDuplicates": refined.get("duplicates").cloned().unwrap_or(serde_json::Value::Null),
        "refinedStats": refined.get("keptStats").cloned().unwrap_or(serde_json::Value::Null),
        "keptCount": finalized.get("keptCount").cloned().unwrap_or(serde_json::Value::Null),
        "removedCount": finalized
            .get("removed")
            .and_then(|v| v.as_array())
            .map(|v| v.len())
            .unwrap_or(0),
    }))
}
