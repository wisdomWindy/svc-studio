//! 离线导入：把用户目录里的权重按清单搬进受管目录，逐文件校验 sha256。
//!
//! 规则（A.11）：源文件只读，不移动、不删除；目标先写临时文件再改名。

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::core::assets::manifest::{verify_file, AssetEntry, AssetFile, FileVerdict};
use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ImportProblem {
    pub rel_path: String,
    pub expected_sha256: String,
    pub verdict: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ImportReport {
    pub asset_id: String,
    pub imported: Vec<String>,
    pub already_ok: Vec<String>,
    pub problems: Vec<ImportProblem>,
}

impl ImportReport {
    pub fn is_complete(&self) -> bool {
        self.problems.is_empty()
    }
}

/// 在源目录里找文件：优先按清单相对路径，其次按文件名（用户常常只给平铺目录）。
fn locate(source_dir: &Path, file: &AssetFile) -> Option<PathBuf> {
    let direct = source_dir.join(&file.rel_path);
    if direct.is_file() {
        return Some(direct);
    }
    let name = Path::new(&file.rel_path).file_name()?;
    let flat = source_dir.join(name);
    if flat.is_file() {
        return Some(flat);
    }
    None
}

fn problem(file: &AssetFile, verdict: &FileVerdict) -> ImportProblem {
    let (verdict_label, detail) = match verdict {
        FileVerdict::Missing => ("missing", "源目录里找不到这个文件".to_string()),
        FileVerdict::SizeMismatch { actual, expected } => (
            "sizeMismatch",
            format!("大小 {actual} 字节，清单要求 {expected} 字节"),
        ),
        FileVerdict::HashMismatch { actual, expected } => (
            "hashMismatch",
            format!("sha256 {actual}，清单要求 {expected}"),
        ),
        FileVerdict::Ok => ("ok", String::new()),
    };
    ImportProblem {
        rel_path: file.rel_path.clone(),
        expected_sha256: file.sha256.clone(),
        verdict: verdict_label.to_string(),
        detail,
    }
}

/// 把一个资产的全部文件导入 `target_root`。
pub fn import_asset(
    entry: &AssetEntry,
    source_dir: &Path,
    target_root: &Path,
) -> AppResult<ImportReport> {
    if !source_dir.is_dir() {
        return Err(Box::new(AppError::new(
            "SVC-3002",
            "导入目录不存在",
            source_dir.display().to_string(),
        )));
    }
    if entry.files.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-2001",
            "清单里该资产没有文件",
            entry.id.clone(),
        )));
    }

    let mut report = ImportReport {
        asset_id: entry.id.clone(),
        imported: Vec::new(),
        already_ok: Vec::new(),
        problems: Vec::new(),
    };

    for file in &entry.files {
        let target = target_root.join(&file.rel_path);
        if target.is_file() && verify_file(&target, file)? == FileVerdict::Ok {
            report.already_ok.push(file.rel_path.clone());
            continue;
        }

        let Some(source) = locate(source_dir, file) else {
            report.problems.push(problem(file, &FileVerdict::Missing));
            continue;
        };

        let verdict = verify_file(&source, file)?;
        if verdict != FileVerdict::Ok {
            report.problems.push(problem(file, &verdict));
            continue;
        }

        copy_verified(&source, &target, file)?;
        report.imported.push(file.rel_path.clone());
    }

    Ok(report)
}

fn copy_verified(source: &Path, target: &Path, file: &AssetFile) -> AppResult<()> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = target.with_extension(format!(
        "{}.tmp",
        target
            .extension()
            .map(|e| e.to_string_lossy().to_string())
            .unwrap_or_default()
    ));
    std::fs::copy(source, &tmp)?;
    let after = verify_file(&tmp, file)?;
    if after != FileVerdict::Ok {
        let _ = std::fs::remove_file(&tmp);
        return Err(Box::new(AppError::new(
            "SVC-2002",
            "写入后校验失败",
            format!("{} -> {}", source.display(), target.display()),
        )));
    }
    std::fs::rename(&tmp, target)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::assets::manifest::{AssetFile, AssetKind};
    use crate::infra::fs::sha256_bytes;

    fn entry(files: Vec<AssetFile>) -> AssetEntry {
        AssetEntry {
            id: "enc".into(),
            kind: AssetKind::Encoder,
            key: "vec768l12".into(),
            version: "v1".into(),
            required: true,
            source_url: None,
            license_note: None,
            files,
        }
    }

    fn file(rel: &str, content: &[u8]) -> AssetFile {
        AssetFile {
            rel_path: rel.into(),
            bytes: content.len() as i64,
            sha256: sha256_bytes(content),
        }
    }

    fn dirs() -> (PathBuf, PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("svcimp-{}", uuid::Uuid::now_v7().simple()));
        let src = base.join("src");
        let dst = base.join("dst");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::create_dir_all(&dst).unwrap();
        (base, src, dst)
    }

    #[test]
    fn imports_nested_layout_and_is_idempotent() {
        let (base, src, dst) = dirs();
        let content = b"weights";
        std::fs::create_dir_all(src.join("pretrain")).unwrap();
        std::fs::write(src.join("pretrain/model.pt"), content).unwrap();

        let report =
            import_asset(&entry(vec![file("pretrain/model.pt", content)]), &src, &dst).unwrap();
        assert!(report.is_complete());
        assert_eq!(report.imported, vec!["pretrain/model.pt".to_string()]);
        assert!(dst.join("pretrain/model.pt").is_file());

        let again =
            import_asset(&entry(vec![file("pretrain/model.pt", content)]), &src, &dst).unwrap();
        assert!(again.imported.is_empty(), "第二次不应重复复制");
        assert_eq!(again.already_ok.len(), 1);
        let _ = std::fs::remove_dir_all(base);
    }

    #[test]
    fn finds_flat_files_by_name() {
        let (base, src, dst) = dirs();
        let content = b"flat";
        std::fs::write(src.join("model.pt"), content).unwrap();
        let report =
            import_asset(&entry(vec![file("pretrain/model.pt", content)]), &src, &dst).unwrap();
        assert!(report.is_complete());
        assert!(dst.join("pretrain/model.pt").is_file());
        let _ = std::fs::remove_dir_all(base);
    }

    #[test]
    fn reports_hash_mismatch_and_writes_nothing() {
        let (base, src, dst) = dirs();
        std::fs::create_dir_all(src.join("pretrain")).unwrap();
        std::fs::write(src.join("pretrain/model.pt"), b"weightz").unwrap();
        let expected = file("pretrain/model.pt", b"weights");
        let report = import_asset(&entry(vec![expected]), &src, &dst).unwrap();
        assert!(!report.is_complete());
        assert_eq!(report.problems[0].verdict, "hashMismatch");
        assert!(!dst.join("pretrain/model.pt").exists());
        let _ = std::fs::remove_dir_all(base);
    }

    #[test]
    fn missing_source_is_reported_not_fatal() {
        let (base, src, dst) = dirs();
        let report =
            import_asset(&entry(vec![file("pretrain/model.pt", b"x")]), &src, &dst).unwrap();
        assert_eq!(report.problems[0].verdict, "missing");
        let _ = std::fs::remove_dir_all(base);
    }
}
