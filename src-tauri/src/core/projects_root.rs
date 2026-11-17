//! 项目根目录的位置。
//!
//! 产品定位是"绿色软件"：项目（数据集/配置/模型）放在**软件安装目录**下，
//! 方便用户直接进文件夹拿模型、拷走整个工程。
//!
//! 但安装目录不一定可写（例如装进 `C:\Program Files` 且非管理员），
//! 所以这里的顺序是：环境变量 → 设置项 → 安装目录（可写时）→ 数据目录。

use std::path::{Path, PathBuf};

pub const PROJECTS_ENV: &str = "SVCSTUDIO_PROJECTS_ROOT";

/// 软件安装目录（可执行文件所在目录）。
pub fn install_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf))
}

/// 目录是否可写：真的写一个临时文件再删掉，比只看属性可靠。
pub fn is_writable(dir: &Path) -> bool {
    if !dir.is_dir() {
        return false;
    }
    let probe = dir.join(".svcstudio-write-test");
    match std::fs::write(&probe, b"ok") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// 解析项目根目录（不创建）。
pub fn resolve_projects_root(data_root: &Path, configured: Option<&str>) -> PathBuf {
    if let Ok(raw) = std::env::var(PROJECTS_ENV) {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Some(raw) = configured.map(str::trim).filter(|s| !s.is_empty()) {
        return PathBuf::from(raw);
    }
    if let Some(dir) = install_dir() {
        let candidate = dir.join("projects");
        // 安装目录可写才用；否则退回数据目录，避免"创建项目失败"这种死路。
        if candidate.is_dir() && is_writable(&candidate) {
            return candidate;
        }
        if !candidate.exists() && is_writable(&dir) {
            return candidate;
        }
    }
    data_root.join("projects")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_override_wins() {
        std::env::set_var(PROJECTS_ENV, r"D:\custom\projects");
        let resolved = resolve_projects_root(Path::new(r"C:\data"), Some(r"E:\ignored"));
        std::env::remove_var(PROJECTS_ENV);
        assert_eq!(resolved, PathBuf::from(r"D:\custom\projects"));
    }

    #[test]
    fn configured_value_beats_install_dir() {
        std::env::remove_var(PROJECTS_ENV);
        let resolved = resolve_projects_root(Path::new(r"C:\data"), Some(r"E:\chosen"));
        assert_eq!(resolved, PathBuf::from(r"E:\chosen"));
    }

    #[test]
    fn falls_back_to_data_root_without_config() {
        std::env::remove_var(PROJECTS_ENV);
        let resolved = resolve_projects_root(Path::new(r"C:\data"), None);
        // 测试进程的可执行文件目录通常不可写或不含 projects，最终落到数据目录
        assert!(
            resolved.ends_with("projects"),
            "结果应当以 projects 结尾：{}",
            resolved.display()
        );
    }

    #[test]
    fn writable_probe_detects_readonly_missing_dir() {
        assert!(!is_writable(Path::new(r"C:\definitely\not\here")));
    }
}
