use serde::{Deserialize, Serialize};
use tauri::State;

use crate::core::ids::model_id;
use crate::core::models::ModelCard;
use crate::core::repo::{models, settings};
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

const DEFAULT_MODEL_KEY: &str = "default_model_id";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterModelInput {
    pub project_id: String,
    pub checkpoint: String,
    pub name: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterModelOutput {
    pub model: ModelCard,
    pub became_default: bool,
    /// 该 checkpoint 之前已登记过（幂等返回）。
    pub already_registered: bool,
}

/// `G_2400.pth` → 2400；其它命名返回 None（不允许登记无法解析步数的文件）。
pub fn step_from_checkpoint(file_name: &str) -> Option<u64> {
    let stem = file_name.strip_suffix(".pth")?;
    let digits = stem.strip_prefix("G_")?;
    digits.parse::<u64>().ok()
}

#[tauri::command]
pub fn register_model(
    state: State<'_, AppPaths>,
    input: RegisterModelInput,
) -> AppResult<RegisterModelOutput> {
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &input.project_id))?
        .ok_or_else(|| {
            Box::new(AppError::new(
                "SVC-7004",
                "项目不存在",
                input.project_id.clone(),
            ))
        })?;
    let root = std::path::PathBuf::from(&project.root_path);
    let checkpoint = std::path::PathBuf::from(&input.checkpoint);
    if !checkpoint.starts_with(&root) {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "模型文件必须位于项目目录内",
            input.checkpoint.clone(),
        )));
    }
    if !checkpoint.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-2001",
            "找不到模型文件",
            input.checkpoint.clone(),
        )));
    }
    let file_name = checkpoint
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    let step = step_from_checkpoint(&file_name).ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-6001",
            "无法识别的 checkpoint 命名",
            format!("{file_name}，应形如 G_2400.pth"),
        ))
    })?;

    let config_path = root.join("configs/config.json");
    let config_bytes = std::fs::read(&config_path).map_err(|_| {
        Box::new(AppError::new(
            "SVC-4001",
            "缺少配置文件",
            config_path.display().to_string(),
        ))
    })?;
    let config: serde_json::Value = serde_json::from_slice(&config_bytes)
        .map_err(|e| Box::new(AppError::new("SVC-4001", "配置文件无法解析", e.to_string())))?;
    let model_section = config.get("model");
    let speech_encoder = model_section
        .and_then(|m| m.get("speech_encoder"))
        .and_then(|v| v.as_str())
        .unwrap_or(&project.speech_encoder)
        .to_string();
    let vocoder = model_section
        .and_then(|m| m.get("vocoder_name"))
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let vol_embedding = model_section
        .and_then(|m| m.get("vol_embedding"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // 同一个 checkpoint 重复登记：直接返回已有记录，不重复插入
    let dedupe_hit = state
        .db
        .with(|c| crate::core::repo::models::find_by_path(c, &checkpoint.to_string_lossy()))?;
    if let Some(existing) = state
        .db
        .with(|c| crate::core::repo::models::find_by_path(c, &input.checkpoint))?
        .or(dedupe_hit)
    {
        return Ok(RegisterModelOutput {
            model: existing,
            became_default: false,
            already_registered: true,
        });
    }

    // 统一模型仓库：训练产物也复制一份到 data/models，与训练目录解耦
    let store_dir = state
        .data_root
        .join("models")
        .join("trained")
        .join(&project.id);
    std::fs::create_dir_all(&store_dir)?;
    let store_weight = store_dir.join(&file_name);
    if !store_weight.is_file()
        || std::fs::metadata(&store_weight)
            .map(|m| m.len())
            .unwrap_or(0)
            != std::fs::metadata(&checkpoint).map(|m| m.len()).unwrap_or(1)
    {
        let tmp = store_dir.join(format!("{file_name}.tmp"));
        std::fs::copy(&checkpoint, &tmp)?;
        std::fs::rename(&tmp, &store_weight)?;
    }
    let store_config = store_dir.join("config.json");
    std::fs::copy(&config_path, &store_config)?;
    let checkpoint = store_weight.clone();
    let config_path = store_config.clone();

    let sha256 = crate::infra::fs::sha256_file(&checkpoint)?;
    let bytes = std::fs::metadata(&checkpoint).ok().map(|m| m.len() as i64);
    let name = input
        .name
        .filter(|n| !n.trim().is_empty())
        .unwrap_or_else(|| format!("{}@{step}", project.name));

    let id = model_id();
    let card = state.db.with(|c| {
        models::insert(
            c,
            &now_iso(),
            &models::NewModel {
                id: &id,
                project_id: Some(&project.id),
                name: &name,
                step: Some(step as i64),
                path: &checkpoint.to_string_lossy(),
                sha256: &sha256,
                bytes,
                speech_encoder: &speech_encoder,
                f0_predictor: Some(&project.f0_predictor),
                vocoder: vocoder.as_deref(),
                vol_embedding,
                config_path: &config_path.to_string_lossy(),
                config_sha256: Some(&crate::infra::fs::sha256_file(&config_path)?),
                dataset_fingerprint: None,
                source: "trained",
            },
        )
    })?;

    let mut became_default = false;
    let existing = state.db.with(|c| settings::get_raw(c, DEFAULT_MODEL_KEY))?;
    if existing.is_none() {
        let value = serde_json::to_string(&card.id).map_err(|e| {
            Box::new(AppError::new(
                "SVC-7003",
                "序列化默认模型失败",
                e.to_string(),
            ))
        })?;
        state
            .db
            .with(|c| settings::set_raw(c, DEFAULT_MODEL_KEY, &value, &now_iso()))?;
        became_default = true;
    }

    Ok(RegisterModelOutput {
        model: card,
        became_default,
        already_registered: false,
    })
}

#[tauri::command]
pub fn list_models(state: State<'_, AppPaths>) -> AppResult<Vec<ModelCard>> {
    state.db.with(models::list)
}

#[tauri::command]
pub fn get_model(state: State<'_, AppPaths>, id: String) -> AppResult<Option<ModelCard>> {
    state.db.with(|c| models::get(c, &id))
}

#[tauri::command]
pub fn default_model(state: State<'_, AppPaths>) -> AppResult<Option<String>> {
    let raw = state.db.with(|c| settings::get_raw(c, DEFAULT_MODEL_KEY))?;
    Ok(raw.and_then(|s| serde_json::from_str::<String>(&s).ok()))
}

#[tauri::command]
pub fn set_default_model(state: State<'_, AppPaths>, id: String) -> AppResult<()> {
    let model = state
        .db
        .with(|c| models::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "模型不存在", id.clone())))?;
    if model.status != "ready" && model.status != "compressed" {
        return Err(Box::new(AppError::new(
            "SVC-6001",
            "该模型当前不可用于推理",
            format!("status={}", model.status),
        )));
    }
    let value = serde_json::to_string(&id).map_err(|e| {
        Box::new(AppError::new(
            "SVC-7003",
            "序列化默认模型失败",
            e.to_string(),
        ))
    })?;
    state
        .db
        .with(|c| settings::set_raw(c, DEFAULT_MODEL_KEY, &value, &now_iso()))
}

/// 删除前做占用检查：默认模型、运行中的推理任务都不允许删。
#[tauri::command]
pub fn delete_model(state: State<'_, AppPaths>, id: String) -> AppResult<()> {
    let default = default_model(state.clone())?;
    if default.as_deref() == Some(id.as_str()) {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "该模型是默认推理模型",
            "先切换默认模型，再删除",
        )));
    }

    // 只有「正在跑/排队」的任务引用该模型时才拒绝删除；历史记录保留在任务表里即可，
    // 不应因此永久锁死模型（用户预期：没有运行中的任务就能删）。
    let busy: i64 = state.db.with(|c| {
        Ok(c.query_row(
            "SELECT count(*) FROM jobs
             WHERE status IN ('running','queued')
               AND payload_json LIKE '%' || ?1 || '%'",
            [&id],
            |r| r.get(0),
        )?)
    })?;
    if busy > 0 {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "该模型仍被正在使用",
            format!("有 {busy} 个排队或运行中的任务引用了它；等任务结束或取消后再删除"),
        )));
    }

    state.db.with(|c| models::delete(c, &id))
}

/// 把历史登记的模型（路径散落在 projects/logs 下）统一收进 data/models。
/// 这样归档、滚动删除、项目迁移都不会再让模型失效。
pub fn migrate_into_store(state: &crate::AppPaths) {
    let store_root = state.data_root.join("models");
    let cards = match state.db.with(crate::core::repo::models::list) {
        Ok(v) => v,
        Err(_) => return,
    };
    for card in cards {
        let src = std::path::PathBuf::from(&card.path);
        if src.starts_with(&store_root) {
            continue;
        }
        if !src.is_file() {
            continue;
        }
        let sub = if card.source == "trained" {
            "trained".to_string()
        } else {
            "imports".to_string()
        };
        let dir = store_root.join(&sub).join(
            card.project_id
                .clone()
                .unwrap_or_else(|| "_external".into()),
        );
        if std::fs::create_dir_all(&dir).is_err() {
            continue;
        }
        let name = src
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "model.pth".into());
        let dest = dir.join(&name);
        if !dest.is_file() && std::fs::copy(&src, &dest).is_err() {
            continue;
        }
        let cfg_src = std::path::PathBuf::from(&card.config_path);
        let cfg_dest = dir.join("config.json");
        if !cfg_dest.is_file() && cfg_src.is_file() {
            let _ = std::fs::copy(&cfg_src, &cfg_dest);
        }
        let cfg_path = if cfg_dest.is_file() {
            cfg_dest.to_string_lossy().to_string()
        } else {
            card.config_path.clone()
        };
        let _ = state.db.with(|c| {
            crate::core::repo::models::relink(c, &card.id, &dest.to_string_lossy(), &cfg_path)
        });
        tracing::info!(model = %card.id, to = %dest.display(), "model moved into data/models");
    }
}

#[cfg(test)]
mod tests {
    use super::step_from_checkpoint;

    #[test]
    fn parses_step_from_checkpoint_name() {
        assert_eq!(step_from_checkpoint("G_2400.pth"), Some(2400));
        assert_eq!(step_from_checkpoint("G_0.pth"), Some(0));
        assert_eq!(step_from_checkpoint("release.pth"), None);
        assert_eq!(step_from_checkpoint("G_abc.pth"), None);
        assert_eq!(step_from_checkpoint("G_12.pt"), None);
    }
}
