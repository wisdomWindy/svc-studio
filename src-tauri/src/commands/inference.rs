//! 推理与模型导入命令。
//!
//! 导入 = 上传六步：预检 → staging 复制并复算 → 兼容性判定 → 原子落位 → 登记。
//! 判定失败的文件进 `models/_unrecognized/`，用户可删除或重传，不占正式库。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::State;

use crate::core::ids::model_id;
use crate::core::model::import::{finalize, plan_upload, slug_for, stage_copy, step_from_name};
use crate::core::models::ModelCard;
use crate::core::repo::models;
use crate::core::runtime::resolve_worker_dir;
use crate::core::worker_cli::run_json_with;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::infra::fs::sha256_file;
use crate::AppPaths;

const CHECK_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);

pub async fn check_remote(python: &Path, model: &Path, config: &Path) -> AppResult<Value> {
    let worker = resolve_worker_dir();
    run_json_with(
        python,
        &worker,
        &[
            "model",
            "check",
            "--model",
            &model.to_string_lossy(),
            "--config",
            &config.to_string_lossy(),
        ],
        CHECK_TIMEOUT,
    )
    .await
}

#[tauri::command]
pub async fn model_check(
    state: State<'_, AppPaths>,
    model_path: String,
    config_path: String,
) -> AppResult<Value> {
    let python = crate::commands::settings::python_for_state(&state)?;
    let model = PathBuf::from(model_path);
    let config = PathBuf::from(config_path);
    if !model.is_file() || !config.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-2001",
            "模型或配置文件不存在",
            format!("{} / {}", model.display(), config.display()),
        )));
    }
    check_remote(&python, &model, &config).await
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportModelInput {
    pub paths: Vec<String>,
    /// 上传区勾选"我确认来源可信"；未勾选不启动导入。
    pub trust: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportModelResult {
    pub model: ModelCard,
    pub compat: Value,
    pub ignored: Vec<String>,
    pub became_default: bool,
}

#[tauri::command]
pub async fn import_model_upload(
    state: State<'_, AppPaths>,
    input: ImportModelInput,
) -> AppResult<ImportModelResult> {
    let python = crate::commands::settings::python_for_state(&state)?;
    if !input.trust {
        return Err(Box::new(AppError {
            code: "SVC-6009".to_string(),
            severity: crate::error::Severity::Warning,
            message: "请先确认模型来源可信".to_string(),
            cause: ".pth 是 pickle，加载可能执行代码".to_string(),
            next_action: crate::error::NextAction {
                label: "勾选来源确认".to_string(),
                deeplink: Some("/models".to_string()),
            },
            detail: None,
            retriable: true,
        }));
    }

    let paths: Vec<PathBuf> = input.paths.iter().map(PathBuf::from).collect();
    let plan = plan_upload(&paths)?;
    let weight_sha = sha256_file(&plan.weight)?;

    if let Some(existing) = state.db.with(|c| models::find_by_sha(c, &weight_sha))? {
        return Err(Box::new(AppError {
            code: "SVC-2002".to_string(),
            severity: crate::error::Severity::Warning,
            message: "同一模型已在库中".to_string(),
            cause: format!("sha256={weight_sha} 已登记为 {}", existing.name),
            next_action: crate::error::NextAction {
                label: "查看已有模型".to_string(),
                deeplink: Some("/models".to_string()),
            },
            detail: Some(existing.id),
            retriable: false,
        }));
    }

    let display_name = plan
        .weight
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "model".to_string());
    let slug = slug_for(&display_name, &weight_sha);

    let staging_root = state.data_root.join("models").join(".staging");
    let staged = stage_copy(&plan, &staging_root, &slug)?;

    let compat = match check_remote(&python, &staged.weight, &staged.config).await {
        Ok(v) => v,
        Err(err) => {
            let _ = std::fs::remove_dir_all(&staged.dir);
            return Err(err);
        }
    };

    let verdict = compat
        .get("verdict")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    if verdict != "ready" && verdict != "compressed" {
        // 保留文件供用户排查/删除，但不进正式库。
        let quarantine = state.data_root.join("models").join("_unrecognized");
        let _ = std::fs::create_dir_all(&quarantine);
        let target = quarantine.join(&slug);
        let _ = std::fs::rename(&staged.dir, &target);
        let message = compat
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("无法识别该模型");
        return Err(Box::new(AppError {
            code: if verdict == "unknown" {
                "SVC-1001"
            } else {
                "SVC-6005"
            }
            .to_string(),
            severity: crate::error::Severity::Blocking,
            message: message.to_string(),
            cause: compat
                .get("detail")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or_else(|| compat.get("missingKeys").map(|v| v.to_string()))
                .unwrap_or_else(|| "结构校验未通过".to_string()),
            next_action: crate::error::NextAction {
                label: "查看原因".to_string(),
                deeplink: Some(format!("file://{}", target.display())),
            },
            detail: Some(target.display().to_string()),
            retriable: false,
        }));
    }

    let imports_root = state.data_root.join("models").join("imports");
    let final_weight = finalize(&staged, &imports_root, &slug)?;
    let model_dir = final_weight
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or(final_weight.clone());
    let final_config = model_dir.join(
        staged
            .config
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "config.json".to_string()),
    );

    let encoder = compat
        .get("speechEncoder")
        .and_then(Value::as_str)
        .unwrap_or("vec768l12")
        .to_string();
    let step = step_from_name(
        &final_weight
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
    );
    let name = format!(
        "{display_name}@{}",
        step.map(|s| s.to_string())
            .unwrap_or_else(|| "?".to_string())
    );
    let id = model_id();
    let config_sha = sha256_file(&final_config).ok();

    let card = state.db.with(|c| {
        models::insert(
            c,
            &now_iso(),
            &models::NewModel {
                id: &id,
                project_id: None,
                name: &name,
                step: step.map(|s| s as i64),
                path: &final_weight.to_string_lossy(),
                sha256: &staged.weight_sha256,
                bytes: Some(staged.bytes as i64),
                speech_encoder: &encoder,
                f0_predictor: None,
                vocoder: None,
                vol_embedding: false,
                config_path: &final_config.to_string_lossy(),
                config_sha256: config_sha.as_deref(),
                dataset_fingerprint: None,
                source: "imported",
            },
        )
    })?;

    if verdict == "compressed" {
        state
            .db
            .with(|c| models::set_status(c, &card.id, "compressed"))?;
    }

    let mut became_default = false;
    let existing_default = state
        .db
        .with(|c| crate::core::repo::settings::get_raw(c, "default_model_id"))?;
    if existing_default.is_none() {
        let value = serde_json::to_string(&card.id).unwrap_or_else(|_| "\"\"".to_string());
        state.db.with(|c| {
            crate::core::repo::settings::set_raw(c, "default_model_id", &value, &now_iso())
        })?;
        became_default = true;
    }

    let ignored = plan
        .ignored
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect();

    Ok(ImportModelResult {
        model: card,
        compat,
        ignored,
        became_default,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn model_check_reports_missing_torch_readably() {
        let worker = resolve_worker_dir();
        if !worker.join("svc_bridge").is_dir() {
            eprintln!("skip: worker 源码不在预期位置");
            return;
        }
        let dir = std::env::temp_dir().join(format!("svccheck-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let weight = dir.join("G_1.pth");
        let config = dir.join("config.json");
        std::fs::write(&weight, b"not-a-real-checkpoint").unwrap();
        std::fs::write(
            &config,
            br#"{"model":{"speech_encoder":"vec768l12","ssl_dim":768}}"#,
        )
        .unwrap();

        let value = check_remote(&crate::core::runtime::resolve_python(), &weight, &config)
            .await
            .expect("必须返回结构化结论");
        let verdict = value.get("verdict").and_then(Value::as_str).unwrap();
        assert!(
            verdict == "unknown" || verdict == "incompatible",
            "不可解析的权重应给出 unknown/incompatible，实际 {verdict}"
        );
        assert!(value.get("message").is_some());
        let _ = std::fs::remove_dir_all(dir);
    }
}

/// 启动一次推理批次（后台作业）。
///
/// 参数写进项目 tmp 下的 JSON 文件而不是命令行，避免 Windows 命令行长度与转义问题。
#[tauri::command]
pub fn infer_start(
    state: State<'_, AppPaths>,
    project_id: String,
    model_id: String,
    inputs: Vec<String>,
    params: Value,
    spk: Option<String>,
    batch_name: Option<String>,
) -> AppResult<String> {
    if inputs.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-6004",
            "没有可推理的输入",
            "先选择至少一条音频",
        )));
    }
    let model = state
        .db
        .with(|c| models::get(c, &model_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "模型不存在", model_id.clone())))?;
    if model.status != "ready" && model.status != "compressed" {
        return Err(Box::new(AppError::new(
            "SVC-6001",
            "该模型当前不可用于推理",
            format!("status={}", model.status),
        )));
    }

    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;
    let root = PathBuf::from(&project.root_path);
    let tmp_dir = root.join("tmp");
    std::fs::create_dir_all(&tmp_dir)?;

    let job = crate::core::ids::job_id();
    let run = crate::core::ids::run_id();
    let now = now_iso();
    let params_text = serde_json::to_string_pretty(&params).map_err(|e| {
        Box::new(AppError::new(
            "SVC-4001",
            "推理参数无法序列化",
            e.to_string(),
        ))
    })?;
    let params_path = tmp_dir.join(format!("infer_{job}.json"));
    crate::infra::fs::atomic_write(&params_path, params_text.as_bytes())?;

    let spk = spk.unwrap_or_else(|| model.name.split('@').next().unwrap_or("spk").to_string());
    let batch_name = batch_name.unwrap_or_else(|| "batch".to_string());

    state.db.with(|c| {
        crate::core::repo::jobs::create(
            c,
            &job,
            "inference",
            Some(&project_id),
            &serde_json::json!({"modelId": model_id, "count": inputs.len()}).to_string(),
            &now,
        )?;
        crate::core::repo::jobs::set_status(c, &job, "running", None, &now)
    })?;

    let mut spec = crate::bridge::host::SpawnSpec::new(
        job.clone(),
        crate::commands::settings::python_for_state(&state)?,
    )
    .arg("-m")
    .arg("svc_bridge")
    .arg("infer")
    .arg("--project")
    .arg(root.to_string_lossy().to_string())
    .arg("--model")
    .arg(&model.path)
    .arg("--config")
    .arg(&model.config_path)
    .arg("--params")
    .arg(params_path.to_string_lossy().to_string())
    .arg("--spk")
    .arg(&spk)
    .arg("--batch-name")
    .arg(&batch_name)
    .arg("--inputs");
    for input in &inputs {
        spec = spec.arg(input);
    }
    spec = spec.cwd(root.clone());
    for (k, v) in crate::core::runtime::worker_env(&resolve_worker_dir()) {
        spec = spec.env(k, v);
    }

    let mut cancel = state.running.register(&job);
    let db = state.db.clone();
    let running = state.running.clone();
    let job_for_task = job.clone();
    let params_json_for_task = params_text.clone();

    tauri::async_runtime::spawn(async move {
        let mut handle = match crate::bridge::host::spawn(spec) {
            Ok(h) => h,
            Err(err) => {
                let now = now_iso();
                let _ = db.with(|c| {
                    crate::core::repo::jobs::set_status(
                        c,
                        &job_for_task,
                        "failed",
                        Some(&err.code),
                        &now,
                    )
                });
                running.unregister(&job_for_task);
                return;
            }
        };

        let mut summary = serde_json::json!({"ok": 0, "failed": 0});
        let mut report_path: Option<String> = None;
        loop {
            tokio::select! {
                _ = &mut cancel => {
                    let _ = handle.stop(std::time::Duration::from_secs(5)).await;
                    let now = now_iso();
                    let _ = db.with(|c| crate::core::repo::jobs::set_status(c, &job_for_task, "stopped", None, &now));
                    break;
                }
                event = handle.events().recv() => {
                    match event {
                        None => break,
                        Some(crate::bridge::protocol::WorkerEvent::Progress { step, total, .. }) => {
                            let ratio = if total > 0 { step as f64 / total as f64 } else { 0.0 };
                            let _ = db.with(|c| crate::core::repo::jobs::set_progress(c, &job_for_task, ratio, Some(step), Some(total)));
                        }
                        Some(crate::bridge::protocol::WorkerEvent::Result { data }) => {
                            summary = data.get("summary").cloned().unwrap_or(summary);
                            report_path = data.get("reportPath").and_then(|v| v.as_str()).map(str::to_string);
                        }
                        Some(crate::bridge::protocol::WorkerEvent::Error { code, message, .. }) => {
                            let now = now_iso();
                            let _ = db.with(|c| crate::core::repo::jobs::append_event(
                                c, &job_for_task,
                                &crate::core::repo::jobs::NewJobEvent {
                                    level: "error", code: Some(&code), message: &message, data_json: None, ts: &now,
                                },
                            ));
                        }
                        Some(_) => {}
                    }
                }
            }
        }

        let now = now_iso();
        let ok = summary.get("ok").and_then(Value::as_u64).unwrap_or(0) as i64;
        let failed = summary.get("failed").and_then(Value::as_u64).unwrap_or(0) as i64;
        let status = if failed == 0 && ok > 0 {
            "succeeded"
        } else if ok == 0 {
            "failed"
        } else {
            "succeeded_with_errors"
        };
        // 批量失败时把第一条失败原因写进事件，避免任务页只有 SVC-6001 没有细节
        if status == "failed" {
            if let Some(path) = report_path.as_ref() {
                if let Ok(text) = std::fs::read_to_string(path) {
                    if let Ok(report) = serde_json::from_str::<Value>(&text) {
                        let detail = report
                            .get("items")
                            .and_then(|v| v.as_array())
                            .and_then(|items| {
                                items.iter().find_map(|item| {
                                    let failed = item.get("status").and_then(|s| s.as_str())
                                        == Some("failed");
                                    if !failed {
                                        return None;
                                    }
                                    let input =
                                        item.get("input").and_then(|s| s.as_str()).unwrap_or("");
                                    let error =
                                        item.get("error").and_then(|s| s.as_str()).unwrap_or("");
                                    Some(format!("{input}: {error}"))
                                })
                            })
                            .unwrap_or_else(|| "推理失败（报告里没有具体条目）".to_string());
                        let now = now_iso();
                        let _ = db.with(|c| {
                            crate::core::repo::jobs::append_event(
                                c,
                                &job_for_task,
                                &crate::core::repo::jobs::NewJobEvent {
                                    level: "error",
                                    code: Some("SVC-6001"),
                                    message: &detail,
                                    data_json: None,
                                    ts: &now,
                                },
                            )
                        });
                    }
                }
            }
        }

        let params_hash = crate::infra::fs::sha256_bytes(params_json_for_task.as_bytes());

        let _ = db.with(|c| {
            c.execute(
                "INSERT INTO inference_runs
                 (id,project_id,model_id,batch_name,model_scope,params_json,params_hash,input_count,ok_count,fail_count,status,report_path,job_id,created_at)
                 VALUES (?1,?2,?3,?4,'project',?5,?6,?7,?8,?9,?10,?11,?12,?13)",
                rusqlite::params![
                    run,
                    project_id,
                    model_id,
                    batch_name,
                    params_json_for_task,
                    params_hash,
                    ok + failed,
                    ok,
                    failed,
                    status,
                    report_path,
                    job_for_task,
                    now
                ],
            )?;
            crate::core::repo::jobs::set_status(
                c,
                &job_for_task,
                if status == "failed" { "failed" } else { "succeeded" },
                if status == "failed" { Some("SVC-6001") } else { None },
                &now,
            )?;
            Ok(())
        });

        running.unregister(&job_for_task);
    });

    Ok(job)
}

/// 列出项目的推理批次（从 outputs/*/report.json 读摘要）。
#[tauri::command]
pub fn infer_batches(state: State<'_, AppPaths>, project_id: String) -> AppResult<Vec<Value>> {
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;
    let root = PathBuf::from(&project.root_path).join("outputs");
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(&root) else {
        return Ok(out);
    };
    for entry in entries.flatten() {
        let report = entry.path().join("report.json");
        if !report.is_file() {
            continue;
        }
        let Ok(bytes) = std::fs::read(&report) else {
            continue;
        };
        let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
            continue;
        };
        out.push(serde_json::json!({
            "name": entry.file_name().to_string_lossy(),
            "path": entry.path().to_string_lossy(),
            "summary": value.get("summary").cloned().unwrap_or(Value::Null),
            "createdAt": value.get("createdAt").cloned().unwrap_or(Value::Null),
        }));
    }
    out.sort_by(|a, b| b["name"].as_str().cmp(&a["name"].as_str()));
    Ok(out)
}

/// 读取一个批次的完整报告（A/B 对照与失败清单都基于它）。
#[tauri::command]
pub fn infer_batch_report(
    state: State<'_, AppPaths>,
    project_id: String,
    name: String,
) -> AppResult<Value> {
    if name.contains("..") || name.contains('/') || name.contains('\\') {
        return Err(Box::new(AppError::new("SVC-3002", "批次名不合法", name)));
    }
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;
    // 批次名来自报告目录，必须走 safe_join：拒绝 .. / 盘符 / 绝对路径。
    let outputs = PathBuf::from(&project.root_path).join("outputs");
    let batch_dir = crate::infra::fs::safe_join(&outputs, &name)?;
    let path = batch_dir.join("report.json");
    let bytes = std::fs::read(&path).map_err(|_| {
        Box::new(AppError::new(
            "SVC-6004",
            "找不到批次报告",
            path.display().to_string(),
        ))
    })?;
    serde_json::from_slice(&bytes)
        .map_err(|e| Box::new(AppError::new("SVC-7003", "批次报告无法解析", e.to_string())))
}
