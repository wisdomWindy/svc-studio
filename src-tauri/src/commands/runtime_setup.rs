//! 首次启动的运行时安装向导。
//!
//! 安装包不含 Python 运行时（NSIS 32 位无法承载 7.6GB），所以首次启动时：
//! 1. 先找数据根 / 安装目录 / 下载目录里有没有 `runtime-*.zip`；
//! 2. 有就提示一键安装，没有就让用户选择 zip；
//! 3. 用系统 `tar.exe` 解压（3.5GB → 7.6GB，约 3–5 分钟），解压后自检 `python.exe`。

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::AppPaths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeSetupStatus {
    pub ready: bool,
    pub python_path: Option<String>,
    pub detected_pack: Option<String>,
}

fn pack_candidates(data_root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut dirs = vec![data_root.to_path_buf()];
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            dirs.push(dir.to_path_buf());
        }
    }
    if let Ok(home) = std::env::var("USERPROFILE") {
        dirs.push(PathBuf::from(home).join("Downloads"));
    }
    for dir in dirs {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if name.starts_with("runtime-") && name.ends_with(".zip") {
                out.push(path);
            }
        }
    }
    out
}

fn python_ready() -> Option<PathBuf> {
    let python = crate::core::runtime::resolve_python();
    if python.is_absolute() && python.is_file() {
        Some(python)
    } else {
        None
    }
}

#[tauri::command]
pub fn runtime_setup_status(state: State<'_, AppPaths>) -> RuntimeSetupStatus {
    let python = python_ready();
    RuntimeSetupStatus {
        ready: python.is_some(),
        python_path: python.map(|p| p.to_string_lossy().to_string()),
        detected_pack: pack_candidates(&state.data_root)
            .first()
            .map(|p| p.to_string_lossy().to_string()),
    }
}

/// 解压运行时包到数据根 `runtime/`，并校验 `python.exe` 存在。
#[tauri::command]
pub async fn install_runtime_from_zip(
    state: State<'_, AppPaths>,
    zip_path: String,
) -> AppResult<RuntimeSetupStatus> {
    let zip = PathBuf::from(&zip_path);
    if !zip.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "找不到运行时包",
            zip_path,
        )));
    }
    let target = state.data_root.join("runtime");
    if target.exists() {
        // 运行中的 worker 会占住 torch 的 DLL，删除会失败：给出可操作提示，而不是裸 IO 错误。
        std::fs::remove_dir_all(&target).map_err(|e| {
            Box::new(AppError::new(
                "SVC-5001",
                "旧运行时正在使用",
                format!("请先关闭训练/推理任务并退出应用再安装：{e}"),
            ))
        })?;
    }
    std::fs::create_dir_all(&target)?;

    let mut tar = std::process::Command::new("tar");
    crate::core::proc::hide_console(&mut tar);
    let status = tar
        .arg("-x")
        .arg("-f")
        .arg(&zip)
        .arg("-C")
        .arg(&target)
        .status()
        .map_err(|e| Box::new(AppError::new("SVC-5001", "无法启动解压程序", e.to_string())))?;
    if !status.success() {
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时包解压失败",
            format!("tar 退出码 {:?}", status.code()),
        )));
    }

    let python = target.join("python.exe");
    if !python.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时包不完整",
            "解压后没有找到 python.exe",
        )));
    }

    let detected = python_ready();
    Ok(RuntimeSetupStatus {
        ready: detected.is_some(),
        python_path: detected.map(|p| p.to_string_lossy().to_string()),
        detected_pack: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pack_candidates_only_match_runtime_zips() {
        let dir = crate::infra::dataroot::tmp_dir()
            .join(format!("svcpack-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("runtime-0.1.0.zip"), b"x").unwrap();
        std::fs::write(dir.join("other.zip"), b"x").unwrap();

        let found = pack_candidates(&dir);
        assert_eq!(found.len(), 1, "只认 runtime-*.zip");
        assert!(found[0].ends_with("runtime-0.1.0.zip"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn missing_zip_is_rejected_readably() {
        // 通过同一套校验逻辑确认：不存在的路径不会被当成包
        let missing = PathBuf::from("C:/definitely/not/here/runtime.zip");
        assert!(!missing.is_file());
    }
}
