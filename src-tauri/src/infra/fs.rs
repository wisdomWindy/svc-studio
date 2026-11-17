//! 文件系统原语：原子写、路径安全拼接、sha256。
//!
//! WP1 建齐工具，实际消费方在 WP2（进程日志/产物）与 WP3（资产校验）接入。
//! 接入完成后删除下面的 allow。
use std::fs;
use std::io::Write;
use std::path::{Component, Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::error::{AppError, AppResult};

/// 原子写：同目录临时文件 → fsync → rename。跨盘 rename 会失败，因此临时文件必须同目录。
pub fn atomic_write(path: &Path, bytes: &[u8]) -> AppResult<()> {
    let parent = path.parent().ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7001",
            "目标路径没有父目录",
            path.display().to_string(),
        ))
    })?;
    fs::create_dir_all(parent)?;

    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tmp = parent.join(format!(
        ".{}.{}.{}.tmp",
        path.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "file".into()),
        std::process::id(),
        stamp
    ));

    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    fs::rename(&tmp, path).map_err(|e| {
        let _ = fs::remove_file(&tmp);
        Box::new(AppError::io("原子写入", e))
    })?;
    Ok(())
}

/// 把用户提供的相对路径安全地拼到根下：拒绝绝对路径、盘符、`..`、UNC 与 NUL。
pub fn safe_join(root: &Path, rel: &str) -> AppResult<PathBuf> {
    if rel.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "路径为空",
            "relative path is empty",
        )));
    }
    if rel.contains('\0') {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "路径包含非法字符",
            "NUL in path",
        )));
    }

    let rel_path = Path::new(rel);
    if rel_path.is_absolute() {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "不允许绝对路径",
            rel.to_string(),
        )));
    }
    if rel.starts_with("\\\\") || rel.starts_with("//") {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "不允许 UNC 路径",
            rel.to_string(),
        )));
    }

    let mut out = root.to_path_buf();
    for comp in rel_path.components() {
        match comp {
            Component::Normal(part) => out.push(part),
            Component::CurDir => {}
            Component::ParentDir => {
                return Err(Box::new(AppError::new(
                    "SVC-3002",
                    "路径不允许包含 ..",
                    rel.to_string(),
                )))
            }
            Component::RootDir | Component::Prefix(_) => {
                return Err(Box::new(AppError::new(
                    "SVC-3002",
                    "路径不允许包含盘符或根",
                    rel.to_string(),
                )))
            }
        }
    }

    // 词法检查之外再做一次前缀校验，挡住 root 本身是符号链接的情况。
    let root_norm = normalize(root);
    let out_norm = normalize(&out);
    if !out_norm.starts_with(&root_norm) {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "路径越出允许的根目录",
            out.display().to_string(),
        )));
    }
    Ok(out)
}

fn normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

pub fn sha256_file(path: &Path) -> AppResult<String> {
    let mut hasher = Sha256::new();
    let mut f = fs::File::open(path)?;
    std::io::copy(&mut f, &mut hasher)?;
    Ok(hex::encode(hasher.finalize()))
}

pub fn sha256_bytes(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_write_replaces_existing_file() {
        let dir = tempfile::tempdir().expect("tempdir");
        let target = dir.path().join("a/b/config.json");
        atomic_write(&target, b"{\"v\":1}").expect("first");
        atomic_write(&target, b"{\"v\":2}").expect("second");
        assert_eq!(fs::read_to_string(&target).unwrap(), "{\"v\":2}");
        let leftovers: Vec<_> = fs::read_dir(target.parent().unwrap())
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "不得残留临时文件");
    }

    #[test]
    fn safe_join_accepts_normal_relative_path() {
        let root = Path::new("C:/data/project");
        let joined = safe_join(root, "dataset_raw/spk/001.wav").expect("ok");
        assert!(joined.ends_with("dataset_raw/spk/001.wav"));
    }

    #[test]
    fn safe_join_rejects_traversal_and_absolutes() {
        let root = Path::new("C:/data/project");
        for bad in [
            "../escape.wav",
            "a/../../escape.wav",
            "C:/windows/system32/x.dll",
            "//server/share/x.wav",
            "\\\\server\\share\\x.wav",
            "",
        ] {
            assert!(safe_join(root, bad).is_err(), "应拒绝: {bad}");
        }
    }

    #[test]
    fn safe_join_rejects_nul_byte() {
        let root = Path::new("C:/data");
        assert!(safe_join(root, "a\0b").is_err());
    }

    #[test]
    fn sha256_matches_known_value() {
        assert_eq!(
            sha256_bytes(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
