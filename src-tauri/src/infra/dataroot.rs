//! 数据根解析：环境变量 → 指针文件 → 默认 `%USERPROFILE%\SVCStudio`。
//!
//! 指针文件放在 `%LOCALAPPDATA%\svc-studio\data-root.txt`，由 `scripts/migrate-data.ps1`
//! 写入。切换数据盘时不需要改代码或重装。

use std::path::{Path, PathBuf};

pub const DATA_ROOT_ENV: &str = "SVCSTUDIO_DATA_ROOT";

#[cfg(test)]
pub fn pick_data_root(env_value: Option<&str>, pointer: Option<&str>, home: &Path) -> PathBuf {
    pick_data_root_with(env_value, pointer, home, None)
}

/// 带安装目录的版本：安装目录可写时，数据就放在软件目录里（不拆成两处）。
pub fn pick_data_root_with(
    env_value: Option<&str>,
    pointer: Option<&str>,
    home: &Path,
    install_dir: Option<&Path>,
) -> PathBuf {
    if let Some(raw) = env_value {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Some(raw) = pointer {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Some(dir) = install_dir {
        // 用户要求数据放在安装目录内；卸载误删由 NSIS 的 PREUNINSTALL 钩子把 data/ 移出去兜底。
        if crate::core::projects_root::is_writable(dir) {
            return dir.to_path_buf();
        }
    }
    home.join("SVCStudio")
}

/// 指针文件只放安装目录旁（用户要求：任何文件都不写 C 盘）。
/// 旧版本写在 %LOCALAPPDATA%，这里仅用于「只读迁移」，不再写回。
/// 应用内所有临时文件都落在数据根下的 tmp/（用户要求：不写 C 盘）。
pub fn tmp_dir() -> PathBuf {
    if let Ok(root) = crate::error::data_root() {
        let dir = root.join("tmp");
        if std::fs::create_dir_all(&dir).is_ok() {
            return dir;
        }
    }
    std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|d| d.join("tmp")))
        .unwrap_or_else(|| PathBuf::from("tmp"))
}

pub fn pointer_path() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    Some(exe.parent()?.join("data-root.txt"))
}

fn legacy_pointer_path() -> Option<PathBuf> {
    std::env::var("LOCALAPPDATA")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(|base| PathBuf::from(base).join("svc-studio").join("data-root.txt"))
}

pub fn read_pointer() -> Option<String> {
    let path = pointer_path()
        .filter(|p| p.is_file())
        .or_else(legacy_pointer_path)?;
    let text = std::fs::read_to_string(path).ok()?;
    // 指针文件可能带 UTF-8 BOM 或行尾换行，直接当路径用会报 os error 123
    let cleaned = text.trim_start_matches('\u{feff}').trim().to_string();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_wins_over_pointer_and_default() {
        let home = Path::new("C:/Users/u");
        assert_eq!(
            pick_data_root(Some("D:/data"), Some("E:/x"), home),
            PathBuf::from("D:/data")
        );
    }

    #[test]
    fn pointer_used_when_env_absent_or_blank() {
        let home = Path::new("C:/Users/u");
        assert_eq!(
            pick_data_root(None, Some("D:/program/svcstudio"), home),
            PathBuf::from("D:/program/svcstudio")
        );
        assert_eq!(
            pick_data_root(Some("   "), Some("D:/program/svcstudio"), home),
            PathBuf::from("D:/program/svcstudio")
        );
    }

    #[test]
    fn install_dir_keeps_data_inside() {
        let home = Path::new("C:/Users/u");
        let install = Path::new("D:/program/svcstudio");
        assert_eq!(
            pick_data_root_with(None, None, home, Some(install)),
            PathBuf::from("D:/program/svcstudio")
        );
    }

    #[test]
    fn falls_back_to_home_default() {
        let home = Path::new("C:/Users/u");
        assert_eq!(pick_data_root(None, None, home), home.join("SVCStudio"));
        assert_eq!(
            pick_data_root(Some(""), Some("  "), home),
            home.join("SVCStudio")
        );
    }
}
