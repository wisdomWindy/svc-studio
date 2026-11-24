use serde::Serialize;
use std::path::PathBuf;

use tauri::State;

use crate::core::job_state::JobStatus;
use crate::core::models::{Job, JobEvent, JobStatusCount};
use crate::core::repo::jobs;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

/// 任务列表被前端高频轮询（2.5s 一次）。同步命令会在主线程执行，
/// 一旦 SQLite 被写锁占住就会把整个界面卡死（表现为「未响应」），因此放到阻塞线程池。
#[tauri::command]
pub async fn list_jobs(state: State<'_, AppPaths>, limit: Option<i64>) -> AppResult<Vec<Job>> {
    let limit = limit.unwrap_or(100).clamp(1, 500);
    let db = state.db.clone();
    tauri::async_runtime::spawn_blocking(move || db.with(|c| jobs::list_recent(c, limit)))
        .await
        .map_err(|e| Box::new(AppError::new("SVC-7003", "任务列表读取失败", e.to_string())))?
}

#[tauri::command]
pub fn get_job(state: State<'_, AppPaths>, id: String) -> AppResult<Option<Job>> {
    state.db.with(|c| jobs::get(c, &id))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobEventPage {
    pub events: Vec<JobEvent>,
    pub next_seq: i64,
}

#[tauri::command]
pub async fn job_events(
    state: State<'_, AppPaths>,
    job_id: String,
    after_seq: Option<i64>,
    limit: Option<i64>,
) -> AppResult<JobEventPage> {
    let after = after_seq.unwrap_or(0);
    let limit = limit.unwrap_or(500).clamp(1, 2000);
    let db = state.db.clone();
    let job = job_id.clone();
    let events = tauri::async_runtime::spawn_blocking(move || {
        db.with(|c| jobs::list_events(c, &job, after, limit))
    })
    .await
    .map_err(|e| Box::new(AppError::new("SVC-7003", "事件读取失败", e.to_string())))??;
    let next_seq = events.last().map(|e| e.seq).unwrap_or(after);
    Ok(JobEventPage { events, next_seq })
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobsSummary {
    pub counts: Vec<JobStatusCount>,
    pub running: i64,
}

#[tauri::command]
pub fn jobs_summary(state: State<'_, AppPaths>) -> AppResult<JobsSummary> {
    let counts = state.db.with(jobs::counts_by_status)?;
    let running = counts
        .iter()
        .filter(|c| c.status == "running" || c.status == "stopping")
        .map(|c| c.count)
        .sum();
    Ok(JobsSummary { counts, running })
}

/// 排队中的任务直接取消；运行中的任务先终止进程树，再落状态。
#[tauri::command]
pub async fn cancel_job(state: State<'_, AppPaths>, id: String) -> AppResult<Job> {
    let now = now_iso();
    let job = state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "任务不存在", id.clone())))?;

    match JobStatus::parse(&job.status) {
        Some(JobStatus::Queued) | Some(JobStatus::Blocked) => {
            state
                .db
                .with(|c| jobs::set_status(c, &id, "cancelled", None, &now))?;
        }
        Some(JobStatus::Running) | Some(JobStatus::Stopping) => {
            if state.runner.contains(&id) {
                crate::commands::runner::stop_running(state.inner(), &id, true).await?;
            } else {
                // 进程句柄不在本进程（例如上次运行遗留），按对账语义处理。
                state
                    .db
                    .with(|c| jobs::set_status(c, &id, "interrupted", None, &now))?;
            }
        }
        _ => {
            return Err(Box::new(AppError::new(
                "SVC-7002",
                "该任务已结束，无需取消",
                format!("status={}", job.status),
            )));
        }
    }

    state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7003", "取消后读不回任务", id)))
}

/// 打开该任务所属推理批次所在文件夹（资源管理器）。
#[tauri::command]
pub fn job_open_folder(state: State<'_, AppPaths>, id: String) -> AppResult<String> {
    let batch = latest_batch_of(&state, &id)?;
    let dir = batch.join("outputs");
    let target = if dir.is_dir() { dir } else { batch };
    crate::commands::projects::open_in_explorer(&target)?;
    Ok(target.to_string_lossy().to_string())
}

/// 删除该任务的推理产物（整批音频 + 报告目录）。
#[tauri::command]
pub fn job_delete_output(state: State<'_, AppPaths>, id: String) -> AppResult<String> {
    let batch = latest_batch_of(&state, &id)?;
    std::fs::remove_dir_all(&batch).map_err(|e| {
        Box::new(AppError::new(
            "SVC-6001",
            "删除推理产物失败",
            format!("{}: {e}", batch.display()),
        ))
    })?;
    let now = crate::infra::db::now_iso();
    state.db.with(|c| {
        // 产物已删：在 payload 上打标记，列表里据此隐藏「打开文件夹/删除」
        if let Ok(Some(job)) = jobs::get(c, &id) {
            let mut payload: serde_json::Value =
                serde_json::from_str(&job.payload_json).unwrap_or_else(|_| serde_json::json!({}));
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("outputDeleted".into(), serde_json::Value::Bool(true));
            }
            c.execute(
                "UPDATE jobs SET payload_json = ?1 WHERE id = ?2",
                rusqlite::params![payload.to_string(), id],
            )?;
        }
        jobs::append_event(
            c,
            &id,
            &jobs::NewJobEvent {
                level: "info",
                code: None,
                message: "已删除推理产物",
                data_json: Some(&serde_json::json!({ "batch": batch }).to_string()),
                ts: &now,
            },
        )
    })?;
    Ok(batch.to_string_lossy().to_string())
}

/// 取任务对应项目下最新的推理批次目录。
fn latest_batch_of(state: &AppPaths, id: &str) -> AppResult<PathBuf> {
    let job = state
        .db
        .with(|c| jobs::get(c, id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7002", "任务不存在", id.to_string())))?;

    let project_id = job.project_id.ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7002",
            "任务没有关联项目",
            id.to_string(),
        ))
    })?;
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;

    let outputs_root = PathBuf::from(&project.root_path).join("outputs");
    std::fs::read_dir(&outputs_root)
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-6001",
                "找不到推理产物目录",
                e.to_string(),
            ))
        })?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .max_by_key(|p| {
            std::fs::metadata(p)
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
        })
        .ok_or_else(|| Box::new(AppError::new("SVC-6001", "还没有任何推理批次", "")))
}

#[tauri::command]
pub fn job_open_output(state: State<'_, AppPaths>, id: String) -> AppResult<String> {
    let job = state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7002", "任务不存在", id.clone())))?;

    let project_id = job
        .project_id
        .ok_or_else(|| Box::new(AppError::new("SVC-7002", "任务没有关联项目", id.clone())))?;
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;

    let outputs_root = PathBuf::from(&project.root_path).join("outputs");
    let batch = std::fs::read_dir(&outputs_root)
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-6001",
                "找不到推理产物目录",
                e.to_string(),
            ))
        })?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .max_by_key(|p| {
            std::fs::metadata(p)
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
        })
        .ok_or_else(|| Box::new(AppError::new("SVC-6001", "还没有任何推理批次", "")))?;

    let out_dir = batch.join("outputs");
    let files: Vec<PathBuf> = std::fs::read_dir(&out_dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.is_file())
                .collect()
        })
        .unwrap_or_default();

    // 优先用默认播放器打开第一个音频；没有音频才退回打开文件夹。
    const AUDIO_EXT: &[&str] = &["wav", "flac", "mp3", "m4a", "ogg", "opus", "aac"];
    let audio = files.iter().find(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|e| AUDIO_EXT.contains(&e.to_ascii_lowercase().as_str()))
            .unwrap_or(false)
    });

    let target = audio.cloned().unwrap_or_else(|| out_dir.clone());
    let mut cmd = if audio.is_some() {
        // rundll32 走文件关联，能正确处理中文与括号路径
        let mut cmd = std::process::Command::new("rundll32.exe");
        crate::core::proc::hide_console(&mut cmd);
        cmd.arg("url.dll,FileProtocolHandler");
        cmd.arg(&target);
        cmd
    } else {
        let mut cmd = std::process::Command::new("explorer.exe");
        crate::core::proc::hide_console(&mut cmd);
        cmd.arg(&target);
        cmd
    };
    cmd.spawn()
        .map_err(|e| Box::new(AppError::new("SVC-6001", "无法打开产物", e.to_string())))?;
    Ok(target.to_string_lossy().to_string())
}

/// 批量删除任务记录。运行中/排队中的任务不允许删除（返回被拒绝的 id）。
#[tauri::command]
pub fn delete_jobs(state: State<'_, AppPaths>, ids: Vec<String>) -> AppResult<serde_json::Value> {
    let mut deleted = 0usize;
    let mut refused: Vec<String> = Vec::new();

    for id in &ids {
        let job = state.db.with(|c| jobs::get(c, id))?;
        let Some(job) = job else { continue };
        if job.status == "running" || job.status == "queued" {
            refused.push(id.clone());
            continue;
        }
        // 先让运行中的进程登记表释放，再删库
        let _ = state.running.cancel(id);
        state.db.with(|c| jobs::delete(c, id))?;
        deleted += 1;
    }

    Ok(serde_json::json!({ "deleted": deleted, "refused": refused }))
}
