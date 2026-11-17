use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::core::repo::settings;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsSnapshot {
    pub values: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingUpdate {
    pub key: String,
    pub value: serde_json::Value,
}

const ALLOWED_KEYS: &[&str] = &[
    "theme",
    "close_behavior",
    "gpu_index",
    "log_level",
    "default_model_id",
    "mirror",
    "check_updates",
    "projects_root",
    "assets_root",
];

pub fn validate_key(key: &str) -> AppResult<()> {
    if ALLOWED_KEYS.contains(&key) {
        Ok(())
    } else {
        Err(Box::new(AppError::new(
            "SVC-4001",
            "未知的设置项",
            format!("{key} 不在允许列表"),
        )))
    }
}

#[tauri::command]
pub fn get_settings(state: State<'_, AppPaths>) -> AppResult<SettingsSnapshot> {
    let mut values = BTreeMap::new();
    for key in ALLOWED_KEYS {
        if let Some(raw) = state.db.with(|c| settings::get_raw(c, key))? {
            let parsed = serde_json::from_str(&raw).unwrap_or(serde_json::Value::Null);
            values.insert((*key).to_string(), parsed);
        }
    }
    Ok(SettingsSnapshot { values })
}

#[tauri::command]
pub fn set_setting(state: State<'_, AppPaths>, update: SettingUpdate) -> AppResult<()> {
    validate_key(&update.key)?;
    let raw = serde_json::to_string(&update.value)
        .map_err(|e| Box::new(AppError::new("SVC-4001", "设置值无法序列化", e.to_string())))?;
    state
        .db
        .with(|c| settings::set_raw(c, &update.key, &raw, &now_iso()))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConsentState {
    pub accepted: Vec<String>,
    pub complete: bool,
    pub doc_version: String,
}

const CONSENT_KINDS: &[&str] = &["agpl", "usage", "dataset", "voice"];
const CONSENT_DOC_VERSION: &str = "v1";

#[tauri::command]
pub fn consents_status(state: State<'_, AppPaths>) -> AppResult<ConsentState> {
    let accepted: Vec<String> = state.db.with(|c| {
        let mut stmt = c.prepare("SELECT DISTINCT kind FROM consents")?;
        let rows = stmt.query_map([], |r| r.get::<_, String>(0))?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    })?;
    let complete = CONSENT_KINDS
        .iter()
        .all(|k| accepted.iter().any(|a| a == k));
    Ok(ConsentState {
        accepted,
        complete,
        doc_version: CONSENT_DOC_VERSION.to_string(),
    })
}

/// 首次启动的四项确认。缺任何一项都不算完成；已确认的不会重复写。
#[tauri::command]
pub fn accept_consents(state: State<'_, AppPaths>, kinds: Vec<String>) -> AppResult<ConsentState> {
    for kind in &kinds {
        if !CONSENT_KINDS.contains(&kind.as_str()) {
            return Err(Box::new(AppError::new(
                "SVC-4001",
                "未知的确认项",
                kind.clone(),
            )));
        }
    }
    let now = now_iso();
    let app_version = env!("CARGO_PKG_VERSION");
    state.db.with(|c| {
        for kind in &kinds {
            let exists: i64 = c.query_row(
                "SELECT count(*) FROM consents WHERE kind = ?1",
                [kind],
                |r| r.get(0),
            )?;
            if exists == 0 {
                c.execute(
                    "INSERT INTO consents (id,kind,doc_version,app_version,subject,accepted_at,hash)
                     VALUES (?1,?2,?3,?4,NULL,?5,?6)",
                    rusqlite::params![
                        format!("cs_{}", uuid::Uuid::now_v7().simple()),
                        kind,
                        CONSENT_DOC_VERSION,
                        app_version,
                        now,
                        crate::infra::fs::sha256_bytes(format!("{kind}|{CONSENT_DOC_VERSION}|{now}").as_bytes())
                    ],
                )?;
            }
        }
        Ok(())
    })?;
    consents_status(state)
}

/// 唯一运行时：内置 GPU 运行时（Python 3.13 + torch cu128）。
pub fn python_for_state(_state: &AppPaths) -> AppResult<std::path::PathBuf> {
    let path = crate::core::runtime::resolve_python();
    ensure_bundled(&path)?;
    Ok(path)
}

/// 只允许内置运行时：绝对路径且真实存在；绝不回退系统 `python`。
pub fn ensure_bundled(path: &std::path::Path) -> AppResult<()> {
    if path.is_absolute() && path.is_file() {
        Ok(())
    } else {
        Err(Box::new(AppError::new(
            "SVC-5001",
            "内置 Python 运行时缺失",
            format!(
                "找不到 {}；软件只使用自带运行时，不会退回系统 Python。请重新安装或运行 scripts/build-runtime.ps1",
                path.display()
            ),
        )))
    }
}

#[cfg(test)]
mod runtime_tests {
    use super::*;

    #[test]
    fn ensure_bundled_rejects_plain_python_name() {
        // 系统 python 的典型形态：相对名，没有绝对路径
        let err = ensure_bundled(std::path::Path::new("python")).unwrap_err();
        assert_eq!(err.code, "SVC-5001");
        assert!(err.cause.contains("不会退回系统 Python"));
    }

    #[test]
    fn ensure_bundled_rejects_missing_file() {
        let missing = std::env::temp_dir().join("svc-no-python.exe");
        let _ = std::fs::remove_file(&missing);
        assert!(ensure_bundled(&missing).is_err());
    }

    #[test]
    fn ensure_bundled_accepts_existing_absolute_file() {
        let dir = std::env::temp_dir().join(format!("svcrt-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let exe = dir.join("python.exe");
        std::fs::write(&exe, b"x").unwrap();
        assert!(ensure_bundled(&exe).is_ok());
        std::fs::remove_dir_all(&dir).ok();
    }
}
