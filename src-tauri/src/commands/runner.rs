//! 诊断探针：真实拉起 worker（`python -m svc_bridge rpc`），把事件写入
//! `job_events`、推进进度，并向窗口广播。它同时验证"IPC → spawn → 事件 → DB"整条链路。

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::bridge::host::{spawn, RunningJob, SpawnSpec};
use crate::bridge::protocol::WorkerEvent;
use crate::core::ids::job_id;
use crate::core::models::Job;
use crate::core::repo::jobs;
use crate::error::{AppError, AppResult};
use crate::infra::db::{now_iso, Db};
use crate::AppPaths;

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct JobEventPayload {
    pub job_id: String,
    pub kind: String,
    pub message: String,
    pub step: Option<i64>,
    pub total: Option<i64>,
}

pub fn worker_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("SVCSTUDIO_WORKER_DIR") {
        return PathBuf::from(dir);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("worker")
}

fn python_exe(app: &AppHandle) -> PathBuf {
    if let Ok(p) = std::env::var("SVCSTUDIO_PYTHON") {
        return PathBuf::from(p);
    }
    if let Ok(resource) = app.path().resource_dir() {
        let candidate = resource.join("runtime").join("python.exe");
        if candidate.exists() {
            return candidate;
        }
    }
    PathBuf::from("python")
}

fn persist(db: &Db, job_id: &str, event: &WorkerEvent) -> AppResult<()> {
    let now = now_iso();
    match event {
        WorkerEvent::Progress { step, total, .. } => {
            let progress = if *total > 0 {
                (*step as f64 / *total as f64).clamp(0.0, 1.0)
            } else {
                0.0
            };
            db.with(|c| jobs::set_progress(c, job_id, progress, Some(*step), Some(*total)))?;
            db.with(|c| {
                jobs::append_event(
                    c,
                    job_id,
                    &jobs::NewJobEvent {
                        level: "info",
                        code: None,
                        message: &format!("进度 {step}/{total}"),
                        data_json: None,
                        ts: &now,
                    },
                )
            })?;
        }
        WorkerEvent::Log { level, message } => {
            let lv = match level.as_str() {
                "warn" | "error" | "debug" => level.as_str(),
                _ => "info",
            };
            db.with(|c| {
                jobs::append_event(
                    c,
                    job_id,
                    &jobs::NewJobEvent {
                        level: lv,
                        code: None,
                        message,
                        data_json: None,
                        ts: &now,
                    },
                )
            })?;
        }
        WorkerEvent::Error {
            code,
            message,
            cause,
            ..
        } => {
            db.with(|c| {
                jobs::append_event(
                    c,
                    job_id,
                    &jobs::NewJobEvent {
                        level: "error",
                        code: Some(code),
                        message,
                        data_json: cause.as_deref(),
                        ts: &now,
                    },
                )
            })?;
        }
        WorkerEvent::Hello { protocol, .. } => {
            let mismatch = *protocol != 0 && *protocol != crate::bridge::protocol::PROTOCOL_VERSION;
            let message = if mismatch {
                format!("worker 协议版本 {protocol} 与主进程不一致")
            } else {
                "worker 已握手".to_string()
            };
            db.with(|c| {
                jobs::append_event(
                    c,
                    job_id,
                    &jobs::NewJobEvent {
                        level: if mismatch { "warn" } else { "info" },
                        code: if mismatch { Some("SVC-1003") } else { None },
                        message: &message,
                        data_json: None,
                        ts: &now,
                    },
                )
            })?;
        }
        WorkerEvent::Result { data } => {
            let payload = serde_json::to_string(data).unwrap_or_default();
            db.with(|c| {
                jobs::append_event(
                    c,
                    job_id,
                    &jobs::NewJobEvent {
                        level: "info",
                        code: None,
                        message: "worker 返回结果",
                        data_json: Some(&payload),
                        ts: &now,
                    },
                )
            })?;
        }
        _ => {}
    }
    Ok(())
}

fn spawn_diagnostics(app: &AppHandle, job: &str) -> AppResult<RunningJob> {
    let dir = worker_dir();
    if !dir.exists() {
        return Err(Box::new(AppError::new(
            "SVC-1001",
            "找不到 worker 目录",
            dir.display().to_string(),
        )));
    }
    let spec = SpawnSpec::new(job, python_exe(app))
        .arg("-m")
        .arg("svc_bridge")
        .arg("rpc")
        .arg("--job")
        .arg(job)
        .env("PYTHONPATH", dir.to_string_lossy().to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .env("JOB_ID", job)
        .cwd(dir);
    spawn(spec)
}

fn forward(app: AppHandle, db: Arc<Db>, job: String, mut running: RunningJob) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = running.events().recv().await {
            let payload = JobEventPayload {
                job_id: job.clone(),
                kind: match &event {
                    WorkerEvent::Progress { .. } => "progress".into(),
                    WorkerEvent::Log { .. } => "log".into(),
                    WorkerEvent::Error { .. } => "error".into(),
                    WorkerEvent::Result { .. } => "result".into(),
                    _ => "state".into(),
                },
                message: match &event {
                    WorkerEvent::Log { message, .. } => message.clone(),
                    WorkerEvent::Error { message, .. } => message.clone(),
                    WorkerEvent::Progress { step, total, .. } => format!("{step}/{total}"),
                    _ => String::new(),
                },
                step: match &event {
                    WorkerEvent::Progress { step, .. } => Some(*step),
                    _ => None,
                },
                total: match &event {
                    WorkerEvent::Progress { total, .. } => Some(*total),
                    _ => None,
                },
            };
            let _ = persist(&db, &job, &event);
            let _ = app.emit("job:event", payload);
        }

        let outcome = running.wait().await.ok();
        let now = now_iso();
        let status = match outcome.map(|o| o.code) {
            Some(Some(0)) => "succeeded",
            _ => "failed",
        };
        let _ = db.with(|c| jobs::set_status(c, &job, status, None, &now));
        let _ = app.emit(
            "job:done",
            JobEventPayload {
                job_id: job.clone(),
                kind: "done".into(),
                message: status.to_string(),
                step: None,
                total: None,
            },
        );
    });
}

#[tauri::command]
pub async fn run_diagnostics(app: AppHandle, state: State<'_, AppPaths>) -> AppResult<Job> {
    let id = job_id();
    let now = now_iso();
    let job = state
        .db
        .with(|c| jobs::create(c, &id, "diagnostics", None, r#"{"probe":"bridge"}"#, &now))?;

    let running = spawn_diagnostics(&app, &id)?;
    state.db.with(|c| jobs::set_pid(c, &id, running.pid))?;
    state
        .db
        .with(|c| jobs::set_status(c, &id, "running", None, &now))?;

    let db = Arc::clone(&state.db);
    forward(app, db, id.clone(), running);

    Ok(job)
}

/// 供测试与命令层复用的停止实现。
pub async fn stop_running(state: &AppPaths, id: &str, cancel: bool) -> AppResult<()> {
    let Some(running) = state.runner.take(id) else {
        return Err(Box::new(AppError::new(
            "SVC-7002",
            "该任务没有可终止的进程",
            id.to_string(),
        )));
    };
    let outcome = if cancel {
        running.cancel(Duration::from_secs(2)).await?
    } else {
        running.stop(Duration::from_secs(5)).await?
    };
    let now = now_iso();
    let status = if cancel { "cancelled" } else { "stopped" };
    state
        .db
        .with(|c| jobs::set_status(c, id, status, None, &now))?;
    tracing::info!(job = id, code = ?outcome.code, killed = outcome.killed, "job stopped");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::repo::jobs;
    use crate::infra::db::Db;

    fn fixture() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(&dir.path().join("t.db")).unwrap();
        db.migrate().unwrap();
        (dir, db)
    }

    #[test]
    fn worker_events_are_persisted_and_progress_moves() {
        let (_d, db) = fixture();
        let now = now_iso();
        db.with(|c| jobs::create(c, "job_x", "diagnostics", None, "{}", &now))
            .unwrap();

        persist(
            &db,
            "job_x",
            &WorkerEvent::Progress {
                stage: Some("demo".into()),
                step: 5,
                total: 10,
                losses: Vec::new(),
                lr: None,
                elapsed: None,
                vram_mb: None,
                vram_peak_mb: None,
            },
        )
        .unwrap();
        persist(
            &db,
            "job_x",
            &WorkerEvent::Log {
                level: "warn".into(),
                message: "非协议输出: torch loading".into(),
            },
        )
        .unwrap();
        persist(
            &db,
            "job_x",
            &WorkerEvent::Error {
                code: "SVC-5001".into(),
                message: "oom".into(),
                cause: Some("CUDA out of memory".into()),
                retriable: true,
            },
        )
        .unwrap();

        let job = db.with(|c| jobs::get(c, "job_x")).unwrap().unwrap();
        assert!((job.progress - 0.5).abs() < f64::EPSILON);
        assert_eq!(job.step, Some(5));
        assert_eq!(job.total, Some(10));

        let events = db.with(|c| jobs::list_events(c, "job_x", 0, 10)).unwrap();
        assert_eq!(events.len(), 3);
        assert_eq!(events[1].level, "warn");
        assert_eq!(events[2].code.as_deref(), Some("SVC-5001"));
        assert_eq!(events[2].level, "error");
    }
}
