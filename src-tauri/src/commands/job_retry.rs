//! 任务重试：只允许可重试状态重新排队，其它状态明确拒绝。

use tauri::State;

use crate::core::job_state::JobStatus;
use crate::core::models::Job;
use crate::core::repo::jobs;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

#[tauri::command]
pub fn retry_job(state: State<'_, AppPaths>, id: String) -> AppResult<Job> {
    let now = now_iso();
    let job = state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "任务不存在", id.clone())))?;

    let status = JobStatus::parse(&job.status).ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7002",
            "任务状态无法识别",
            job.status.clone(),
        ))
    })?;
    if !status.is_retriable() {
        return Err(Box::new(AppError::new(
            "SVC-7002",
            "当前状态不能重试",
            format!("status={}", status.as_str()),
        )));
    }

    state.db.with(|c| {
        jobs::set_status(c, &id, "queued", None, &now)?;
        jobs::append_event(
            c,
            &id,
            &jobs::NewJobEvent {
                level: "info",
                code: None,
                message: "已重新排队",
                data_json: None,
                ts: &now,
            },
        )
    })?;

    state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7003", "重试后读不回任务", id)))
}
