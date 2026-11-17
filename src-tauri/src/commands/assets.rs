//! 资产中心命令：清单、状态、离线导入。

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::core::assets::importer::{import_asset, ImportReport};
use crate::core::assets::manifest::{
    self, enforce, parse, verify_file, AssetKind, FileVerdict, SignatureState,
};
use crate::error::{AppError, AppResult};
use crate::AppPaths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestView {
    pub signature: SignatureState,
    pub generated_at: String,
    pub asset_count: usize,
    pub assets: Vec<AssetView>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AssetView {
    pub id: String,
    pub kind: AssetKind,
    pub key: String,
    pub version: String,
    pub required: bool,
    pub source_url: Option<String>,
    pub license_note: Option<String>,
    pub files: Vec<AssetFileStatus>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AssetFileStatus {
    pub rel_path: String,
    pub bytes: i64,
    pub sha256: String,
    pub state: FileVerdict,
}

fn manifest_dir(state: &AppPaths) -> PathBuf {
    state.data_root.join("assets")
}

pub fn load_manifest(state: &AppPaths) -> AppResult<manifest::Manifest> {
    let dir = manifest_dir(state);
    let manifest_path = dir.join("manifest.json");
    let sig_path = dir.join("manifest.sig");

    let bytes = std::fs::read(&manifest_path).map_err(|_| {
        Box::new(AppError {
            code: "SVC-2001".to_string(),
            severity: crate::error::Severity::Blocking,
            message: "资产清单缺失".to_string(),
            cause: manifest_path.display().to_string(),
            next_action: crate::error::NextAction {
                label: "重新安装或导入清单".to_string(),
                deeplink: Some("/settings".to_string()),
            },
            detail: None,
            retriable: false,
        })
    })?;

    let sig = std::fs::read_to_string(&sig_path).ok();
    let signature = manifest::verify_manifest(&bytes, sig.as_deref())?;
    enforce(signature)?;
    parse(&bytes)
}

#[tauri::command]
pub fn assets_manifest(state: State<'_, AppPaths>) -> AppResult<ManifestView> {
    let manifest = load_manifest(&state)?;
    let dir = manifest_dir(&state);
    let bytes = std::fs::read(dir.join("manifest.json"))?;
    let sig = std::fs::read_to_string(dir.join("manifest.sig")).ok();
    let signature = manifest::verify_manifest(&bytes, sig.as_deref())?;

    let assets: Vec<AssetView> = manifest
        .assets
        .into_iter()
        .map(|a| AssetView {
            id: a.id,
            kind: a.kind,
            key: a.key,
            version: a.version,
            required: a.required,
            source_url: a.source_url,
            license_note: a.license_note,
            files: a
                .files
                .into_iter()
                .map(|f| {
                    let verdict =
                        verify_file(&dir.join(&f.rel_path), &f).unwrap_or(FileVerdict::Missing);
                    AssetFileStatus {
                        rel_path: f.rel_path,
                        bytes: f.bytes,
                        sha256: f.sha256,
                        state: verdict,
                    }
                })
                .collect(),
        })
        .collect();

    Ok(ManifestView {
        signature,
        generated_at: manifest.generated_at,
        asset_count: assets.len(),
        assets,
    })
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportAssetInput {
    pub asset_id: String,
    pub source_dir: String,
}

/// 离线导入：源目录只读，目标目录受管；逐文件 sha256 校验后原子落位。
#[tauri::command]
pub fn import_asset_dir(
    state: State<'_, AppPaths>,
    input: ImportAssetInput,
) -> AppResult<ImportReport> {
    let manifest = load_manifest(&state)?;
    let entry = manifest
        .assets
        .into_iter()
        .find(|a| a.id == input.asset_id)
        .ok_or_else(|| {
            Box::new(AppError::new(
                "SVC-2001",
                "清单里没有这个资产",
                input.asset_id.clone(),
            ))
        })?;
    let report = import_asset(
        &entry,
        &PathBuf::from(&input.source_dir),
        &manifest_dir(&state),
    )?;
    if report.is_complete() {
        tracing::info!(asset = %report.asset_id, "asset import complete");
    } else {
        tracing::warn!(
            asset = %report.asset_id,
            problems = report.problems.len(),
            "asset import incomplete"
        );
    }
    Ok(report)
}
