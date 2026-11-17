//! 模型工具：压缩、ONNX 导出、声线混合计划。
//!
//! 三者都走一次性 worker CLI（短任务，不需要事件流）。真实耗时由上游脚本决定，
//! 因此超时给足；失败时保留上游输出尾部，便于定位。

use std::path::PathBuf;

use serde::Deserialize;
use serde_json::Value;
use tauri::State;

use crate::core::model::import::slug_for;
use crate::core::repo::models;
use crate::core::runtime::{resolve_upstream_dir, resolve_worker_dir};
use crate::core::worker_cli::run_json_with;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::infra::fs::sha256_file;
use crate::AppPaths;

const TOOL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(600);

fn model_or_err(state: &AppPaths, id: &str) -> AppResult<crate::core::models::ModelCard> {
    state
        .db
        .with(|c| models::get(c, id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "模型不存在", id.to_string())))
}

/// 压缩产物默认落在数据根 models 下，避免写进项目目录造成"多个项目各一份"。
pub fn compress_target(
    data_root: &std::path::Path,
    model: &crate::core::models::ModelCard,
) -> PathBuf {
    let slug = slug_for(&model.name, &model.sha256);
    data_root
        .join("models")
        .join(format!("{slug}-compressed.pth"))
}

#[tauri::command]
pub async fn model_compress(
    state: State<'_, AppPaths>,
    model_id: String,
    half: Option<bool>,
) -> AppResult<Value> {
    let model = model_or_err(&state, &model_id)?;
    let target = compress_target(&state.data_root, &model);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let upstream = resolve_upstream_dir();
    let args = vec![
        "modeltool".to_string(),
        "compress".to_string(),
        "--project".to_string(),
        state.data_root.to_string_lossy().to_string(),
        "--upstream".to_string(),
        upstream.to_string_lossy().to_string(),
        "--config".to_string(),
        model.config_path.clone(),
        "--input".to_string(),
        model.path.clone(),
        "--output".to_string(),
        target.to_string_lossy().to_string(),
    ];
    let mut args = args;
    if half.unwrap_or(false) {
        args.push("--half".to_string());
    }
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = run_json_with(
        &crate::commands::settings::python_for_state(&state)?,
        &resolve_worker_dir(),
        &refs,
        TOOL_TIMEOUT,
    )
    .await?;

    if result.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(Box::new(AppError::new(
            "SVC-6001",
            "模型压缩失败",
            result
                .get("stderrTail")
                .and_then(Value::as_str)
                .unwrap_or("上游脚本未产出文件")
                .to_string(),
        )));
    }

    // 登记为独立模型：压缩版与原版共用 config，但模型文件不同。
    let sha = sha256_file(&target)?;
    let bytes = std::fs::metadata(&target)?.len() as i64;
    let now = now_iso();
    let name = format!("{}（压缩）", model.name);
    let path_text = target.to_string_lossy().to_string();
    let id = crate::core::ids::model_id();
    let new = crate::core::repo::models::NewModel {
        id: &id,
        project_id: model.project_id.as_deref(),
        name: &name,
        step: model.step,
        path: &path_text,
        sha256: &sha,
        bytes: Some(bytes),
        speech_encoder: &model.speech_encoder,
        f0_predictor: model.f0_predictor.as_deref(),
        vocoder: model.vocoder.as_deref(),
        vol_embedding: model.vol_embedding,
        config_path: &model.config_path,
        config_sha256: model.config_sha256.as_deref(),
        dataset_fingerprint: model.dataset_fingerprint.as_deref(),
        source: "compressed",
    };
    let card = state.db.with(|c| models::insert(c, &now, &new))?;
    Ok(serde_json::json!({ "model": card, "result": result }))
}

#[tauri::command]
pub async fn model_onnx(state: State<'_, AppPaths>, model_id: String) -> AppResult<Value> {
    let model = model_or_err(&state, &model_id)?;
    let project_id = model.project_id.clone().ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-6004",
            "ONNX 导出需要项目",
            "导入的模型没有项目目录，请先在某个项目里登记该模型再导出",
        ))
    })?;
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id)))?;

    let name = slug_for(&model.name, &model.sha256);
    let upstream = resolve_upstream_dir().to_string_lossy().to_string();
    let args = [
        "modeltool",
        "onnx",
        "--project",
        &project.root_path,
        "--upstream",
        &upstream,
        "--name",
        &name,
    ];
    let result = run_json_with(
        &crate::commands::settings::python_for_state(&state)?,
        &resolve_worker_dir(),
        &args,
        TOOL_TIMEOUT,
    )
    .await?;
    Ok(result)
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MixModelInput {
    pub model_id: String,
    pub weight: f64,
}

/// 只做"能不能混"的校验与权重归一，不落盘、不改模型。
#[tauri::command]
pub async fn mix_preview(
    state: State<'_, AppPaths>,
    models_input: Vec<MixModelInput>,
    mode: Option<String>,
) -> AppResult<Value> {
    if models_input.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "没有选择要混合的模型",
            "至少选 2 个单说话人模型",
        )));
    }
    let mut entries = Vec::new();
    for item in &models_input {
        let model = model_or_err(&state, &item.model_id)?;
        let speakers = 1; // 单说话人：多说话人模型在下面由 worker 按字段拒绝
        entries.push(serde_json::json!({
            "path": model.path,
            "name": model.name,
            "speakerCount": speakers,
            "encoder": model.speech_encoder,
            "sslDim": serde_json::Value::Null,
            "ginChannels": serde_json::Value::Null,
        }));
    }
    let plan = serde_json::json!({
        "models": entries,
        "mode": mode.unwrap_or_else(|| "convex".to_string()),
        "weights": models_input.iter().map(|m| m.weight).collect::<Vec<_>>(),
    });

    let tmp = state
        .data_root
        .join("tmp")
        .join(format!("mix-{}.json", uuid::Uuid::now_v7().simple()));
    if let Some(parent) = tmp.parent() {
        std::fs::create_dir_all(parent)?;
    }
    crate::infra::fs::atomic_write(
        &tmp,
        &serde_json::to_vec_pretty(&plan).map_err(|e| {
            Box::new(AppError::new(
                "SVC-4001",
                "混合计划无法序列化",
                e.to_string(),
            ))
        })?,
    )?;

    let result = run_json_with(
        &crate::commands::settings::python_for_state(&state)?,
        &resolve_worker_dir(),
        &["mix", "--input", &tmp.to_string_lossy()],
        std::time::Duration::from_secs(60),
    )
    .await;
    let _ = std::fs::remove_file(&tmp);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::ModelCard;

    fn card(name: &str) -> ModelCard {
        ModelCard {
            id: "mdl_x".into(),
            project_id: None,
            name: name.into(),
            step: None,
            path: "C:/m.pth".into(),
            sha256: "abcdef1234567890".into(),
            bytes: Some(1),
            speech_encoder: "vec768l12".into(),
            f0_predictor: None,
            vocoder: None,
            vol_embedding: false,
            config_path: "C:/c.json".into(),
            config_sha256: None,
            dataset_fingerprint: None,
            train_run_id: None,
            diffusion_path: None,
            cluster_path: None,
            index_path: None,
            source: "trained".into(),
            status: "ready".into(),
            mixed: false,
            compat_json: None,
            origin_dir: None,
            copied: true,
            required_assets_json: None,
            created_at: String::new(),
        }
    }

    #[test]
    fn compressed_target_is_stable_and_namespaced() {
        let root = std::path::Path::new("C:/data");
        let first = compress_target(root, &card("小满 音色"));
        let second = compress_target(root, &card("小满 音色"));
        assert_eq!(first, second, "同一模型产物路径必须稳定");
        assert!(first.starts_with(root.join("models")));
        assert_eq!(first.extension().unwrap(), "pth");
    }
}
