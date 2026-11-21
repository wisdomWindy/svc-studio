//! 安装路径变更时的数据迁移。
//!
//! 数据（runtime / pretrain / projects / models / assets / logs / studio.db）默认放在安装目录内。
//! 用户把程序装到新目录时，旧目录里的数据不会自动跟过去；这里在启动时把旧位置的数据搬到
//! 当前数据根，并在 `%LOCALAPPDATA%\svc-studio\data-root.txt` 记录当前位置。

use std::path::{Path, PathBuf};

use crate::error::AppResult;

/// 需要迁移的条目（目录或文件）。
const DATA_ITEMS: [&str; 7] = [
    "projects",
    "runtime",
    "pretrain",
    "models",
    "assets",
    "logs",
    "studio.db",
];

pub fn pointer_path(local_appdata: &Path) -> PathBuf {
    local_appdata.join("svc-studio").join("data-root.txt")
}

fn is_empty_of_data(root: &Path) -> bool {
    DATA_ITEMS.iter().all(|name| {
        let p = root.join(name);
        !p.exists()
            || std::fs::read_dir(&p)
                .map(|mut it| it.next().is_none())
                .unwrap_or(true)
    })
}

fn has_any_data(root: &Path) -> bool {
    DATA_ITEMS.iter().any(|name| {
        let p = root.join(name);
        p.exists()
            && std::fs::read_dir(&p)
                .map(|mut it| it.next().is_some())
                .unwrap_or(true)
    })
}

fn move_item(src: &Path, dst: &Path) -> std::io::Result<()> {
    if !dst.exists() && std::fs::rename(src, dst).is_ok() {
        return Ok(());
    }
    // 跨卷或目标已存在：递归复制后删除源
    if src.is_dir() {
        copy_dir(src, dst)?;
        std::fs::remove_dir_all(src)
    } else {
        if let Some(parent) = dst.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::copy(src, dst)?;
        std::fs::remove_file(src)
    }
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let target = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &target)?;
        } else {
            std::fs::copy(entry.path(), &target)?;
        }
    }
    Ok(())
}

/// 若记录的数据根与当前根不同，且当前根还没有数据，则把旧数据搬过来。
/// 返回实际迁移的条目名。
pub fn migrate_if_needed(current_root: &Path, pointer: &Path) -> AppResult<Vec<String>> {
    let mut moved = Vec::new();
    let previous = std::fs::read_to_string(pointer)
        .ok()
        .map(|s| PathBuf::from(s.trim().to_string()))
        .filter(|p| p.is_dir());

    if let Some(prev) = previous {
        let same = prev
            .canonicalize()
            .ok()
            .zip(current_root.canonicalize().ok())
            .map(|(a, b)| a == b)
            .unwrap_or(false);
        if !same && has_any_data(&prev) && is_empty_of_data(current_root) {
            std::fs::create_dir_all(current_root)?;
            for name in DATA_ITEMS {
                let src = prev.join(name);
                if !src.exists() {
                    continue;
                }
                let dst = current_root.join(name);
                if let Ok(()) = move_item(&src, &dst) {
                    moved.push(name.to_string());
                }
            }
        }
    }

    if let Some(parent) = pointer.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(pointer, current_root.to_string_lossy().as_bytes())?;
    Ok(moved)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let dir = crate::infra::dataroot::tmp_dir()
            .join(format!("svc-migrate-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn moves_data_from_previous_root() {
        let base = tmp("move");
        let old = base.join("old");
        let new = base.join("new");
        std::fs::create_dir_all(old.join("projects").join("p1")).unwrap();
        std::fs::write(old.join("projects").join("p1").join("a.txt"), b"x").unwrap();
        std::fs::create_dir_all(new.join("runtime")).unwrap();
        let pointer = base.join("data-root.txt");
        std::fs::write(&pointer, old.to_string_lossy().as_bytes()).unwrap();

        let moved = migrate_if_needed(&new, &pointer).unwrap();

        assert!(moved.contains(&"projects".to_string()));
        assert!(new.join("projects").join("p1").join("a.txt").is_file());
        assert!(!old.join("projects").exists());
        assert_eq!(
            std::fs::read_to_string(&pointer).unwrap().trim(),
            new.to_string_lossy()
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn keeps_current_data_untouched() {
        let base = tmp("keep");
        let new = base.join("new");
        std::fs::create_dir_all(new.join("projects").join("mine")).unwrap();
        std::fs::write(new.join("projects").join("mine").join("b.txt"), b"y").unwrap();
        let pointer = base.join("data-root.txt");
        std::fs::write(&pointer, new.to_string_lossy().as_bytes()).unwrap();

        let moved = migrate_if_needed(&new, &pointer).unwrap();

        assert!(moved.is_empty());
        assert!(new.join("projects").join("mine").join("b.txt").is_file());
        let _ = std::fs::remove_dir_all(&base);
    }
}
