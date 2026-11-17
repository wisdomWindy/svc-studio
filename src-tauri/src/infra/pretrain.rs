//! 项目级 `pretrain/` 挂载。
//!
//! 上游脚本用**相对路径** `pretrain/...` 找编码器/F0/声码器，而 worker 以项目根为 cwd 启动，
//! 所以权重必须出现在 `project/pretrain`。全局只保留一份（数据根 `pretrain/`），
//! 项目侧用目录联接（junction）指过去，避免每个项目复制 ~600MB。

use std::path::Path;

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PretrainLink {
    /// 新建了联接/符号链接
    Linked,
    /// 项目里已经有 pretrain（真实目录或链接），未改动
    AlreadyPresent,
    /// 全局 pretrain 不存在（还没导入权重）
    GlobalMissing,
    /// 创建失败（权限/文件系统不支持）；调用方应提示用户
    Failed,
}

pub fn link_pretrain(project_root: &Path, data_root: &Path) -> PretrainLink {
    let global = data_root.join("pretrain");
    let local = project_root.join("pretrain");
    if local.exists() {
        return PretrainLink::AlreadyPresent;
    }
    if !global.is_dir() {
        return PretrainLink::GlobalMissing;
    }

    #[cfg(windows)]
    {
        // mklink /J 不需要管理员权限；symlink_dir 需要开发者模式或提权。
        let mut link = std::process::Command::new("cmd");
        crate::core::proc::hide_console(&mut link);
        let status = link
            .args(["/C", "mklink", "/J"])
            .arg(&local)
            .arg(&global)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        match status {
            Ok(s) if s.success() && local.exists() => PretrainLink::Linked,
            _ => match std::os::windows::fs::symlink_dir(&global, &local) {
                Ok(()) => PretrainLink::Linked,
                Err(_) => PretrainLink::Failed,
            },
        }
    }

    #[cfg(not(windows))]
    {
        match std::os::unix::fs::symlink(&global, &local) {
            Ok(()) => PretrainLink::Linked,
            Err(_) => PretrainLink::Failed,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("svcpre-{name}-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn reports_missing_global_pretrain() {
        let root = temp("missing");
        let project = root.join("p");
        std::fs::create_dir_all(&project).unwrap();
        assert_eq!(link_pretrain(&project, &root), PretrainLink::GlobalMissing);
    }

    #[test]
    fn does_not_touch_existing_project_pretrain() {
        let root = temp("existing");
        std::fs::create_dir_all(root.join("pretrain")).unwrap();
        let project = root.join("p");
        std::fs::create_dir_all(project.join("pretrain")).unwrap();
        assert_eq!(link_pretrain(&project, &root), PretrainLink::AlreadyPresent);
    }

    #[test]
    fn links_when_global_exists() {
        let root = temp("link");
        std::fs::create_dir_all(root.join("pretrain")).unwrap();
        std::fs::write(root.join("pretrain/rmvpe.pt"), b"x").unwrap();
        let project = root.join("p");
        std::fs::create_dir_all(&project).unwrap();

        let outcome = link_pretrain(&project, &root);
        assert_eq!(outcome, PretrainLink::Linked, "应创建链接而不是复制");
        assert!(
            project.join("pretrain/rmvpe.pt").is_file(),
            "链接后项目侧应能读到全局权重"
        );
    }
}
