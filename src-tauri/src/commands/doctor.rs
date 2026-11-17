//! 环境自检命令：拉起 worker 的 `doctor`，解析成结构化报告。
//!
//! doctor 是一次性 CLI（单 JSON 对象），不走进程事件协议——它必须在 torch 都不可用时也能出结果。

use std::collections::BTreeMap;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::State;
use tokio::process::Command;

use crate::core::runtime::{resolve_worker_dir, worker_env};
use crate::error::{AppError, AppResult};
use crate::AppPaths;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DoctorItem {
    pub id: String,
    pub state: String,
    pub message: String,
    #[serde(default)]
    pub cause: String,
    #[serde(default)]
    pub fix: String,
    #[serde(default)]
    pub detail: BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    pub blocking: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DoctorSummary {
    pub pass: i64,
    pub warn: i64,
    pub fail: i64,
    pub trainable: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DoctorReport {
    pub schema: String,
    #[serde(alias = "generated_at")]
    pub generated_at: String,
    #[serde(alias = "data_root")]
    pub data_root: String,
    pub items: Vec<DoctorItem>,
    pub summary: DoctorSummary,
}

pub fn parse_doctor_json(bytes: &[u8]) -> AppResult<DoctorReport> {
    let report: DoctorReport = serde_json::from_slice(bytes).map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "环境自检结果无法解析",
            format!("{e}；原始输出可能被控制台编码破坏"),
        ))
    })?;
    if report.items.len() != 9 {
        return Err(Box::new(AppError::new(
            "SVC-1001",
            "环境自检项数量不正确",
            format!("期望 9 项，实际 {}", report.items.len()),
        )));
    }
    Ok(report)
}

pub async fn run_doctor(
    python: &std::path::Path,
    worker_dir: &std::path::Path,
    data_root: &std::path::Path,
    encoder: &str,
    f0: &str,
) -> AppResult<DoctorReport> {
    let mut cmd = Command::new(python);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args([
        "-m",
        "svc_bridge",
        "doctor",
        "--json",
        "--data-root",
        &data_root.to_string_lossy(),
        "--encoder",
        encoder,
        "--f0",
        f0,
    ]);
    for (k, v) in worker_env(worker_dir) {
        cmd.env(k, v);
    }

    let output = tokio::time::timeout(Duration::from_secs(30), cmd.output())
        .await
        .map_err(|_| {
            Box::new(AppError::new(
                "SVC-1001",
                "环境自检超时",
                "30 秒内没有返回结果",
            ))
        })?
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-1001",
                "无法启动 Python 运行时",
                format!("{}: {e}", python.display()),
            ))
        })?;

    if !output.status.success() {
        return Err(Box::new(AppError::new(
            "SVC-1001",
            "环境自检进程异常退出",
            format!(
                "exit={:?}; stderr={}",
                output.status.code(),
                String::from_utf8_lossy(&output.stderr)
                    .chars()
                    .take(500)
                    .collect::<String>()
            ),
        )));
    }

    parse_doctor_json(&output.stdout)
}

#[tauri::command]
pub async fn doctor_report(
    state: State<'_, AppPaths>,
    encoder: Option<String>,
    f0: Option<String>,
) -> AppResult<DoctorReport> {
    // 自检必须和实际执行用同一个运行时，否则会出现"自检说没 GPU、训练却用 GPU"的分裂。
    let python = crate::commands::settings::python_for_state(&state)?;
    let worker_dir = resolve_worker_dir();
    let encoder = encoder.unwrap_or_else(|| "vec768l12".to_string());
    let f0 = f0.unwrap_or_else(|| "rmvpe".to_string());
    let report = run_doctor(&python, &worker_dir, &state.data_root, &encoder, &f0).await?;
    // 缓存结论，供训练/预处理入口做门禁（B1/B2）。
    let blocking: Vec<String> = report
        .items
        .iter()
        .filter(|i| i.blocking || i.state == "fail")
        .map(|i| i.id.clone())
        .collect();
    if let Some(gpu) = report.items.iter().find(|i| i.id == "gpu") {
        let name = gpu
            .detail
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&gpu.message)
            .to_string();
        let vram = gpu
            .detail
            .get("vram_mb")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        if gpu.state == "pass" {
            if let Ok(mut slot) = state.gpu.lock() {
                *slot = Some((name, vram));
            }
        }
    }
    if let Ok(mut slot) = state.doctor.lock() {
        *slot = Some(crate::core::gate::DoctorGate {
            trainable: report.summary.trainable,
            blocking,
        });
    }
    Ok(report)
}

/// 读取最近一次自检结论（未跑过返回 null），训练页据此决定按钮是否可用。
#[tauri::command]
pub fn doctor_cached(state: State<'_, AppPaths>) -> Option<crate::core::gate::DoctorGate> {
    state.doctor.lock().ok().and_then(|g| g.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Vec<u8> {
        let items: Vec<String> = (0..9)
            .map(|i| {
                format!(
                    r#"{{"id":"c{i}","state":"pass","message":"ok","cause":"","fix":"","detail":{{}},"blocking":false}}"#
                )
            })
            .collect();
        format!(
            r#"{{"schema":"svcstudio.doctor/1","generatedAt":"2026-01-01T00:00:00Z","dataRoot":"C:/d","items":[{}],"summary":{{"pass":9,"warn":0,"fail":0,"trainable":true}}}}"#,
            items.join(",")
        )
        .into_bytes()
    }

    #[test]
    fn parses_nine_item_report() {
        let report = parse_doctor_json(&sample()).expect("parse");
        assert_eq!(report.items.len(), 9);
        assert!(report.summary.trainable);
    }

    #[test]
    fn rejects_truncated_report() {
        let err = parse_doctor_json(b"{\"schema\":\"x\"").unwrap_err();
        assert_eq!(err.code, "SVC-1001");
    }

    #[test]
    fn rejects_wrong_item_count() {
        let bad = br#"{"schema":"s","generatedAt":"t","dataRoot":"d","items":[],"summary":{"pass":0,"warn":0,"fail":0,"trainable":false}}"#;
        let err = parse_doctor_json(bad).unwrap_err();
        assert!(err.cause.contains("9"));
    }

    #[tokio::test]
    async fn runs_real_worker_and_parses_nine_items() {
        // 测试固定用源码 worker：staged 副本可能滞后于本次改动。
        let worker = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("worker");
        if !worker.join("svc_bridge").is_dir() {
            eprintln!("skip: worker 源码不在预期位置 ({})", worker.display());
            return;
        }
        let dir = std::env::temp_dir().join(format!("svcdoctor-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();

        let report = run_doctor(
            &crate::core::runtime::resolve_python(),
            &worker,
            &dir,
            "vec768l12",
            "rmvpe",
        )
        .await
        .expect("doctor 必须跑通并返回结构化报告");

        assert_eq!(report.items.len(), 9);
        let blocking_fail = report.items.iter().any(|i| i.state == "fail" && i.blocking);
        assert_eq!(report.summary.trainable, !blocking_fail);
        for item in &report.items {
            if item.state == "fail" {
                assert!(!item.fix.is_empty(), "失败项 {} 必须带修复动作", item.id);
            }
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
