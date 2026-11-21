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
        // 数据目录必须放在安装目录「旁边」，绝不能放里面：
        // NSIS 卸载会删掉整个安装目录，放里面就会连数据集和模型一起被删。
        if crate::core::projects_root::is_writable(dir) {
            if let (Some(parent), Some(name)) = (dir.parent(), dir.file_name()) {
                let mut sibling = std::ffi::OsString::from(name);
                sibling.push("-data");
                return parent.join(sibling);
            }
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
    fn install_dir_maps_to_sibling_not_inside() {
        let home = Path::new("C:/Users/u");
        let install = Path::new("D:/program/svcstudio");
        let picked = pick_data_root_with(None, None, home, Some(install));
        assert_eq!(picked, PathBuf::from("D:/program/svcstudio-data"));
        assert!(!picked.starts_with(install), "数据目录不能在安装目录内部");
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
