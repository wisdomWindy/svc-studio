//! 数据集命令：扫描、QC（含落库与报告落盘）。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::State;

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
    run_json(
        &python,
        &["dataset", "scan", "--root", &dir.to_string_lossy()],
    )
    .await
}

/// QC 一次 = 数据集的一个版本；文件清单、报告、断言结果都落库。
#[tauri::command]
pub async fn dataset_qc(state: State<'_, AppPaths>, project_id: String) -> AppResult<QcReport> {
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
    let value = run_json(
        &python,
        &[
            "dataset",
            "qc",
            "--root",
            &dir.to_string_lossy(),
            "--out",
            &report_path.to_string_lossy(),
        ],
    )
    .await?;

    let report: QcReport = serde_json::from_value(value).map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "QC 报告结构不符合预期",
            e.to_string(),
        ))
    })?;

    let ds_id = dataset_id();
    let now = now_iso();
    let summary: DatasetSummary = state.db.with(|c| {
        let ds = datasets::insert(c, &ds_id, &project_id, &dir.to_string_lossy(), &now)?;
        for file in &report.files {
            let abs = dir.join(&file.rel_path);
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
        let fingerprint = fingerprint_of(&report.files);
        datasets::finalize(
            c,
            &ds_id,
            report.summary.file_count,
            report.summary.total_seconds,
            &fingerprint,
            report.summary.low_data,
        )?;
        c.execute(
            "INSERT INTO qc_reports (id,dataset_id,json_path,passed,blocking_count,warning_count,generated_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7)",
            rusqlite::params![
                event_id(),
                ds_id,
                report_path.to_string_lossy(),
                i64::from(report.summary.passed),
                report.summary.blocking,
                report.summary.warnings,
                now
            ],
        )?;
        Ok(ds)
    })?;

    // 报告文件再写一次规范化 JSON（保证磁盘与返回值一致）。
    let serialized = serde_json::to_vec_pretty(&report)
        .map_err(|e| Box::new(AppError::new("SVC-7001", "报告序列化失败", e.to_string())))?;
    atomic_write(&report_path, &serialized)?;

    tracing::info!(dataset = %summary.id, files = summary.file_count, "qc done");
    Ok(report)
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
