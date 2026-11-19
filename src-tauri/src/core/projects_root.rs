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
    // 统一放数据目录：项目（数据集/模型）属于用户资产，
    // 不能放在会被卸载/覆盖安装清理的安装目录里。
    let _ = install_dir();
    data_root.join("projects")
}

#[cfg(test)]
mod tests {
    use super::*;

    // 这三个用例都改环境变量，合并成一个以免并行执行互相干扰。
    #[test]
    fn resolution_order_and_fallback() {
        std::env::remove_var(PROJECTS_ENV);

        // 配置值优先于数据目录默认值
        assert_eq!(
            resolve_projects_root(Path::new(r"C:\data"), Some(r"E:\chosen")),
            PathBuf::from(r"E:\chosen")
        );

        // 环境变量优先于配置值
        std::env::set_var(PROJECTS_ENV, r"D:\custom\projects");
        assert_eq!(
            resolve_projects_root(Path::new(r"C:\data"), Some(r"E:\ignored")),
            PathBuf::from(r"D:\custom\projects")
        );
        std::env::remove_var(PROJECTS_ENV);

        // 没有配置时落在数据目录
        assert_eq!(
            resolve_projects_root(Path::new(r"C:\data"), None),
            PathBuf::from(r"C:\data\projects")
        );
    }

    #[test]
    fn writable_probe_detects_readonly_missing_dir() {
        assert!(!is_writable(Path::new(r"C:\definitely\not\here")));
    }
}
