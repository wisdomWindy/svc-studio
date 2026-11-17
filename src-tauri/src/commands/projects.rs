use serde::{Deserialize, Serialize};
use tauri::State;

use crate::core::ids::{project_id, slugify};
use crate::core::models::Project;
use crate::core::repo::projects::{self, NewProject};
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;
use std::path::PathBuf;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateProjectInput {
    pub name: String,
    pub speaker: Option<String>,
    pub speech_encoder: String,
    pub f0_predictor: String,
    pub material_type: String,
    pub root_path: Option<String>,
    pub gpu_index: Option<i64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateProjectOutput {
    pub project: Project,
    pub created_dirs: Vec<String>,
}

const ENCODERS: &[&str] = &[
    "vec768l12",
    "vec256l9",
    "hubertsoft",
    "whisper-ppg",
    "whisper-ppg-large",
    "cnhubertlarge",
    "dphubert",
    "wavlmbase+",
];
const F0: &[&str] = &["crepe", "pm", "dio", "harvest", "rmvpe", "fcpe"];

pub fn validate_encoder(value: &str) -> AppResult<()> {
    if ENCODERS.contains(&value) {
        Ok(())
    } else {
        Err(Box::new(AppError::new(
            "SVC-4001",
            "不支持的语音编码器",
            format!("{value} 不在 {}", ENCODERS.join("/")),
        )))
    }
}

pub fn validate_f0(value: &str) -> AppResult<()> {
    if F0.contains(&value) {
        Ok(())
    } else {
        Err(Box::new(AppError::new(
            "SVC-4001",
            "不支持的 F0 预测器",
            format!("{value} 不在 {}", F0.join("/")),
        )))
    }
}

#[tauri::command]
pub fn create_project(
    state: State<'_, AppPaths>,
    input: CreateProjectInput,
) -> AppResult<CreateProjectOutput> {
    let name = input.name.trim();
    if name.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "项目名不能为空",
            "name is empty",
        )));
    }
    if !matches!(input.material_type.as_str(), "singing" | "speech") {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "素材类型只能是 singing 或 speech",
            input.material_type.clone(),
        )));
    }
    validate_encoder(&input.speech_encoder)?;
    validate_f0(&input.f0_predictor)?;

    let slug = slugify(name);
    let root = match input.root_path.as_deref().map(str::trim) {
        Some(p) if !p.is_empty() => std::path::PathBuf::from(p),
        _ => {
            let configured = state
                .db
                .with(|c| crate::core::repo::settings::get_raw(c, "projects_root"))
                .ok()
                .flatten()
                .and_then(|raw| serde_json::from_str::<String>(&raw).ok());
            crate::core::projects_root::resolve_projects_root(
                &state.data_root,
                configured.as_deref(),
            )
            .join(&slug)
        }
    };

    // 安装目录内只读子树不允许作为项目根（PRD §5.1）。
    let root_str = root.to_string_lossy().replace('\\', "/").to_lowercase();
    for forbidden in ["/runtime/", "/assets/", "/configs_template/"] {
        if root_str.contains(forbidden) {
            return Err(Box::new(AppError::new(
                "SVC-3002",
                "项目根不能放在程序子树内",
                format!("{} 命中只读目录 {forbidden}", root.display()),
            )));
        }
    }

    let mut created = Vec::new();
    for dir in [
        "configs",
        "filelists",
        "dataset_raw",
        "dataset/44k",
        "logs/44k",
        "raw",
        "results",
        "quarantine",
        "reports",
        ".svcstudio",
    ] {
        let p = root.join(dir);
        std::fs::create_dir_all(&p)?;
        created.push(dir.to_string());
    }

    let id = project_id();
    let now = now_iso();
    let project = state.db.with(|c| {
        projects::create(
            c,
            &id,
            &now,
            &NewProject {
                name,
                slug: &slug,
                root_path: &root.to_string_lossy(),
                speaker: input.speaker.as_deref(),
                speech_encoder: &input.speech_encoder,
                f0_predictor: &input.f0_predictor,
                material_type: &input.material_type,
                gpu_index: input.gpu_index,
            },
        )
    })?;

    // 上游用相对路径找 pretrain：项目侧挂到全局权重，避免每个项目复制 ~600MB。
    let _ = crate::infra::pretrain::link_pretrain(&root, &state.data_root);

    Ok(CreateProjectOutput {
        project,
        created_dirs: created,
    })
}

#[tauri::command]
pub fn list_projects(state: State<'_, AppPaths>) -> AppResult<Vec<Project>> {
    state.db.with(projects::list)
}

#[tauri::command]
pub fn get_project(state: State<'_, AppPaths>, id: String) -> AppResult<Option<Project>> {
    state.db.with(|c| projects::get(c, &id))
}

/// 只删目录内的数据库记录；是否删除工作区文件由调用方显式传入，且必须位于数据根下。
#[tauri::command]
pub fn delete_project(state: State<'_, AppPaths>, id: String, delete_files: bool) -> AppResult<()> {
    let project = state
        .db
        .with(|c| projects::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", id.clone())))?;

    if delete_files {
        let root = std::path::PathBuf::from(&project.root_path);
        let data_root = state.data_root.clone();
        if !root.starts_with(&data_root) {
            return Err(Box::new(AppError::new(
                "SVC-7001",
                "该项目目录不在数据目录内，拒绝自动删除",
                project.root_path.clone(),
            )));
        }
        if root.exists() {
            std::fs::remove_dir_all(&root)?;
        }
    }

    state.db.with(|c| projects::delete(c, &id))
}

/// 在资源管理器中打开项目目录（只允许打开已登记的项目根目录）。
#[tauri::command]
pub fn project_open_dir(state: State<'_, AppPaths>, id: String) -> AppResult<()> {
    let project = state
        .db
        .with(|c| projects::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", id.clone())))?;
    let root = PathBuf::from(&project.root_path);
    open_in_explorer(&root)
}

fn open_in_explorer(path: &std::path::Path) -> AppResult<()> {
    if !path.is_dir() {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "项目目录不存在",
            path.display().to_string(),
        )));
    }
    let mut cmd = std::process::Command::new("explorer");
    crate::core::proc::hide_console(&mut cmd);
    cmd.arg(path);
    cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-7004",
            "无法打开资源管理器",
            e.to_string(),
        ))
    })?;
    Ok(())
}

#[cfg(test)]
mod open_tests {
    use super::*;

    #[test]
    fn rejects_missing_directory() {
        let missing = std::env::temp_dir().join("svc-no-such-project-dir");
        let err = open_in_explorer(&missing).unwrap_err();
        assert_eq!(err.code, "SVC-7004");
    }

    #[test]
    fn accepts_existing_directory() {
        let dir = std::env::temp_dir().join(format!("svcproj-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(open_in_explorer(&dir).is_ok());
        std::fs::remove_dir_all(&dir).ok();
    }
}
