//! 诊断包导出：doctor + 版本 + 日志尾部 → 白名单 zip，并自检一遍。

use std::path::PathBuf;

use tauri::State;

use crate::commands::doctor::{run_doctor, DoctorReport};
use crate::core::diagnostics::{audit_package, build, DiagnosticsInput, PackageReport};
use crate::core::runtime::{resolve_python, resolve_worker_dir};
use crate::error::AppResult;
use crate::AppPaths;

fn newest_log_text(data_root: &std::path::Path) -> Option<String> {
    let dir = data_root.join("logs");
    let mut newest: Option<(std::time::SystemTime, PathBuf)> = None;
    for entry in std::fs::read_dir(&dir).ok()? {
        let entry = entry.ok()?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("log") {
            continue;
        }
        let modified = entry.metadata().ok()?.modified().ok()?;
        if newest.as_ref().map(|(t, _)| modified > *t).unwrap_or(true) {
            newest = Some((modified, path));
        }
    }
    let (_, path) = newest?;
    std::fs::read_to_string(path).ok()
}

fn versions_json(state: &AppPaths, doctor: &DoctorReport) -> String {
    let payload = serde_json::json!({
        "appVersion": env!("CARGO_PKG_VERSION"),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "dbVersion": state.db.version,
        "python": resolve_python().to_string_lossy(),
        "workerDir": resolve_worker_dir().to_string_lossy(),
        "doctorSchema": doctor.schema,
        "doctorGeneratedAt": doctor.generated_at,
    });
    payload.to_string()
}

#[tauri::command]
pub async fn export_diagnostics(state: State<'_, AppPaths>) -> AppResult<PackageReport> {
    let python = resolve_python();
    let worker = resolve_worker_dir();
    let doctor = run_doctor(&python, &worker, &state.data_root, "vec768l12", "rmvpe").await?;

    let doctor_json = serde_json::to_string(&doctor).map_err(|e| {
        Box::new(crate::error::AppError::new(
            "SVC-7001",
            "自检报告序列化失败",
            e.to_string(),
        ))
    })?;

    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
    let out = state
        .data_root
        .join("diagnostics")
        .join(format!("SVCStudio-diagnostics-{stamp}.zip"));

    let input = DiagnosticsInput {
        doctor_json: Some(doctor_json),
        versions_json: Some(versions_json(&state, &doctor)),
        config_snapshot: Some(
            serde_json::json!({
                "dataRoot": state.data_root.to_string_lossy(),
                "reconciledJobs": state.reconciled,
            })
            .to_string(),
        ),
        manifest_check: Some(
            serde_json::json!({
                "path": state.data_root.join("assets/manifest.json").to_string_lossy(),
                "present": state.data_root.join("assets/manifest.json").exists(),
            })
            .to_string(),
        ),
        log_tail: newest_log_text(&state.data_root),
        extras: Vec::new(),
    };

    let report = build(&out, &input)?;
    // 打包后立即自检：任何非白名单条目都视为失败，不把可疑包交给用户。
    audit_package(&out)?;
    Ok(report)
}
