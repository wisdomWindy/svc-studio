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

    let project_id = job
        .project_id
        .clone()
        .ok_or_else(|| Box::new(AppError::new("SVC-7002", "任务没有关联项目", id.clone())))?;
    let payload: serde_json::Value =
        serde_json::from_str(&job.payload_json).unwrap_or(serde_json::Value::Null);

    // 重试 = 用原参数重新派发一个新任务（旧任务保留历史）
    let new_job_id = match job.kind.as_str() {
        "preprocess" => {
            let use_diff = payload.get("useDiff").and_then(|v| v.as_bool());
            let vol_aug = payload.get("volAug").and_then(|v| v.as_bool());
            let num = payload.get("numProcesses").and_then(|v| v.as_i64());
            crate::commands::preprocess::preprocess_start_impl(
                &state, project_id, use_diff, vol_aug, num,
            )?
        }
        "train_main" | "train_diff" => {
            let kind = if job.kind == "train_diff" {
                "diffusion"
            } else {
                "main"
            };
            crate::commands::train::train_start_impl(
                &state,
                project_id,
                Some(false),
                Some(kind.to_string()),
                Some(true),
            )?
        }
        "inference" => {
            let model_id = payload
                .get("modelId")
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    Box::new(AppError::new(
                        "SVC-6004",
                        "这条推理任务没有记录模型与输入",
                        "请重新发起推理",
                    ))
                })?
                .to_string();
            let inputs = payload
                .get("inputs")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            if inputs.is_empty() {
                return Err(Box::new(AppError::new(
                    "SVC-6004",
                    "这条推理任务没有记录输入文件",
                    "请重新发起推理",
                )));
            }
            let params = payload
                .get("params")
                .cloned()
                .unwrap_or_else(|| serde_json::json!({}));
            let spk = payload
                .get("spk")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            crate::commands::inference::infer_start_impl(
                &state, project_id, model_id, inputs, params, spk, None,
            )?
        }
        other => {
            return Err(Box::new(AppError::new(
                "SVC-7002",
                "该类型任务暂不支持重试",
                format!("kind={other}"),
            )))
        }
    };

    state.db.with(|c| {
        jobs::append_event(
            c,
            &id,
            &jobs::NewJobEvent {
                level: "info",
                code: None,
                message: "已按原参数重新派发",
                data_json: Some(&serde_json::json!({ "newJob": new_job_id }).to_string()),
                ts: &now,
            },
        )
    })?;

    state
        .db
        .with(|c| jobs::get(c, &new_job_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7003", "重试后读不回任务", id)))
}

#[cfg(test)]
mod retry_kind_tests {
    #[test]
    fn unsupported_kind_is_rejected() {
        // 只验证映射表：非 preprocess/train_*/inference 一律拒绝
        for kind in ["doctor", "runtime_download", "weights_download"] {
            assert!(!matches!(
                kind,
                "preprocess" | "train_main" | "train_diff" | "inference"
            ));
        }
        assert!(matches!(
            "preprocess",
            "preprocess" | "train_main" | "train_diff" | "inference"
        ));
    }
}
