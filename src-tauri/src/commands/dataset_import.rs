//! 把用户选中的音频导入受管目录 `<project>/dataset_raw/<speaker>/`。
//!
//! 只做"复制 + 计数"，不做格式转换（转换由重采样步骤负责）。

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{AppError, AppResult};

const AUDIO_EXTS: &[&str] = &[
    "wav", "flac", "mp3", "m4a", "aac", "ogg", "opus", "wma", "aiff", "aif",
];

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ImportOutcome {
    pub imported: usize,
    pub skipped: usize,
    pub rejected: usize,
    pub target_dir: String,
}

pub fn is_audio(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| AUDIO_EXTS.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// 递归收集候选文件；目录只下潜一层子目录（dataset_raw/<speaker>/ 的常见形态）。
pub fn collect_sources(paths: &[PathBuf]) -> (Vec<PathBuf>, usize) {
    let mut files = Vec::new();
    let mut rejected = 0usize;
    for path in paths {
        if path.is_dir() {
            let Ok(entries) = std::fs::read_dir(path) else {
                rejected += 1;
                continue;
            };
            for entry in entries.flatten() {
                let child = entry.path();
                if child.is_dir() {
                    if let Ok(inner) = std::fs::read_dir(&child) {
                        for sub in inner.flatten() {
                            let f = sub.path();
                            if f.is_file() && is_audio(&f) {
                                files.push(f);
                            } else if f.is_file() {
                                rejected += 1;
                            }
                        }
                    }
                } else if is_audio(&child) {
                    files.push(child);
                } else {
                    rejected += 1;
                }
            }
        } else if path.is_file() && is_audio(path) {
            files.push(path.clone());
        } else {
            rejected += 1;
        }
    }
    (files, rejected)
}

/// 复制到目标目录；同名且同大小视为已存在（跳过），同名不同大小加序号。
pub fn import_files(sources: &[PathBuf], target: &Path) -> AppResult<ImportOutcome> {
    std::fs::create_dir_all(target)?;
    let mut imported = 0usize;
    let mut skipped = 0usize;

    for src in sources {
        let Some(name) = src.file_name() else {
            continue;
        };
        let mut dest = target.join(name);
        if dest.exists() {
            let same_size = std::fs::metadata(&dest)
                .map(|m| m.len())
                .ok()
                .zip(std::fs::metadata(src).map(|m| m.len()).ok())
                .map(|(a, b)| a == b)
                .unwrap_or(false);
            if same_size {
                skipped += 1;
                continue;
            }
            let stem = src.file_stem().and_then(|s| s.to_str()).unwrap_or("audio");
            let ext = src.extension().and_then(|s| s.to_str()).unwrap_or("wav");
            let mut index = 1;
            loop {
                let candidate = target.join(format!("{stem}_{index}.{ext}"));
                if !candidate.exists() {
                    dest = candidate;
                    break;
                }
                index += 1;
            }
        }
        std::fs::copy(src, &dest).map_err(|e| {
            Box::new(AppError::new(
                "SVC-4002",
                "复制音频失败",
                format!("{} → {}: {e}", src.display(), dest.display()),
            ))
        })?;
        imported += 1;
    }

    Ok(ImportOutcome {
        imported,
        skipped,
        rejected: 0,
        target_dir: target.to_string_lossy().to_string(),
    })
}

/// 目标目录：优先项目里登记的说话人名，否则 `spk`。
pub fn speaker_dir(dataset_raw: &Path, speaker: Option<&str>) -> PathBuf {
    let name = speaker
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("spk");
    dataset_raw.join(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("svcimp-{name}-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn collects_audio_and_rejects_others() {
        let src = temp("collect");
        std::fs::write(src.join("a.wav"), b"x").unwrap();
        std::fs::write(src.join("b.txt"), b"x").unwrap();
        let nested = src.join("sub");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::write(nested.join("c.flac"), b"x").unwrap();

        let (files, rejected) = collect_sources(std::slice::from_ref(&src));
        assert_eq!(files.len(), 2, "wav + 嵌套 flac");
        assert_eq!(rejected, 1, "txt 被拒");
        std::fs::remove_dir_all(src).ok();
    }

    #[test]
    fn import_skips_same_size_and_renames_conflict() {
        let src = temp("import-src");
        let dst = temp("import-dst");
        std::fs::write(src.join("a.wav"), b"1234").unwrap();

        let first = import_files(&[src.join("a.wav")], &dst).unwrap();
        assert_eq!((first.imported, first.skipped), (1, 0));

        let second = import_files(&[src.join("a.wav")], &dst).unwrap();
        assert_eq!(
            (second.imported, second.skipped),
            (0, 1),
            "同大小视为已存在"
        );

        // 同名不同大小 → 加序号保存，不覆盖
        std::fs::write(src.join("a.wav"), b"123456").unwrap();
        let third = import_files(&[src.join("a.wav")], &dst).unwrap();
        assert_eq!(third.imported, 1);
        assert!(dst.join("a_1.wav").exists());

        std::fs::remove_dir_all(src).ok();
        std::fs::remove_dir_all(dst).ok();
    }

    #[test]
    fn speaker_dir_falls_back_to_spk() {
        let base = Path::new("C:/p/dataset_raw");
        assert_eq!(speaker_dir(base, None), base.join("spk"));
        assert_eq!(speaker_dir(base, Some("  ")), base.join("spk"));
        assert_eq!(speaker_dir(base, Some("xiaoman")), base.join("xiaoman"));
    }
}
