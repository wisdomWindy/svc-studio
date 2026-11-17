//! 数据根解析：环境变量 → 指针文件 → 默认 `%USERPROFILE%\SVCStudio`。
//!
//! 指针文件放在 `%LOCALAPPDATA%\svc-studio\data-root.txt`，由 `scripts/migrate-data.ps1`
//! 写入。切换数据盘时不需要改代码或重装。

use std::path::{Path, PathBuf};

pub const DATA_ROOT_ENV: &str = "SVCSTUDIO_DATA_ROOT";

pub fn pick_data_root(env_value: Option<&str>, pointer: Option<&str>, home: &Path) -> PathBuf {
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
    home.join("SVCStudio")
}

pub fn pointer_path() -> Option<PathBuf> {
    std::env::var("LOCALAPPDATA")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(|base| PathBuf::from(base).join("svc-studio").join("data-root.txt"))
}

pub fn read_pointer() -> Option<String> {
    let path = pointer_path()?;
    std::fs::read_to_string(path).ok()
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
    fn falls_back_to_home_default() {
        let home = Path::new("C:/Users/u");
        assert_eq!(pick_data_root(None, None, home), home.join("SVCStudio"));
        assert_eq!(
            pick_data_root(Some(""), Some("  "), home),
            home.join("SVCStudio")
        );
    }
}
