use serde::Serialize;
use tauri::State;

use crate::core::job_state::JobStatus;
use crate::core::models::{Job, JobEvent, JobStatusCount};
use crate::core::repo::jobs;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

#[tauri::command]
pub fn list_jobs(state: State<'_, AppPaths>, limit: Option<i64>) -> AppResult<Vec<Job>> {
    let limit = limit.unwrap_or(100).clamp(1, 500);
    state.db.with(|c| jobs::list_recent(c, limit))
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
pub fn job_events(
    state: State<'_, AppPaths>,
    job_id: String,
    after_seq: Option<i64>,
    limit: Option<i64>,
) -> AppResult<JobEventPage> {
    let after = after_seq.unwrap_or(0);
    let limit = limit.unwrap_or(500).clamp(1, 2000);
    let events = state
        .db
        .with(|c| jobs::list_events(c, &job_id, after, limit))?;
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
