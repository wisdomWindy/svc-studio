//! 上传导入：文件分类、1:1 配对、staging 复制（源文件只读）、原子落位。

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{AppError, AppResult};
use crate::infra::fs::sha256_file;

const WEIGHT_EXTS: &[&str] = &["pth", "pt", "ckpt"];
const CONFIG_EXT: &str = "json";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadPlan {
    pub weight: PathBuf,
    pub config: PathBuf,
    pub ignored: Vec<PathBuf>,
}

fn ext_lower(path: &Path) -> String {
    path.extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default()
}

pub fn is_weight(path: &Path) -> bool {
    WEIGHT_EXTS.contains(&ext_lower(path).as_str())
}

/// 只认 1 个权重 + 1 个配置；其它扩展名忽略并记录（不静默吃掉）。
pub fn plan_upload(paths: &[PathBuf]) -> AppResult<UploadPlan> {
    let mut weights = Vec::new();
    let mut configs = Vec::new();
    let mut ignored = Vec::new();

    for path in paths {
        if !path.is_file() {
            ignored.push(path.clone());
            continue;
        }
        let ext = ext_lower(path);
        if is_weight(path) {
            weights.push(path.clone());
        } else if ext == CONFIG_EXT
            && path
                .file_name()
                .map(|n| n.to_string_lossy().contains("config"))
                .unwrap_or(false)
        {
            configs.push(path.clone());
        } else {
            ignored.push(path.clone());
        }
    }

    if weights.len() != 1 || configs.len() != 1 {
        return Err(Box::new(AppError {
            code: "SVC-6008".to_string(),
            severity: crate::error::Severity::Blocking,
            message: "请上传模型和配置文件".to_string(),
            cause: format!(
                "收到 {} 个权重、{} 个 config.json（一次只能导入一个模型）",
                weights.len(),
                configs.len()
            ),
            next_action: crate::error::NextAction {
                label: "重新选择".to_string(),
                deeplink: Some("/models".to_string()),
            },
            detail: None,
            retriable: false,
        }));
    }

    Ok(UploadPlan {
        weight: weights.remove(0),
        config: configs.remove(0),
        ignored,
    })
}

pub fn slug_for(name: &str, sha256: &str) -> String {
    let base: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    let trimmed = base.trim_matches('-');
    let prefix = if trimmed.is_empty() { "model" } else { trimmed };
    format!("{}-{}", prefix, &sha256[..8.min(sha256.len())])
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StagedCopy {
    pub dir: PathBuf,
    pub weight: PathBuf,
    pub config: PathBuf,
    pub weight_sha256: String,
    pub config_sha256: String,
    pub bytes: u64,
}

/// 复制到 staging 并复算 sha256；源文件不移动、不修改。
pub fn stage_copy(plan: &UploadPlan, staging_root: &Path, slug: &str) -> AppResult<StagedCopy> {
    let dir = staging_root.join(slug);
    if dir.exists() {
        std::fs::remove_dir_all(&dir).map_err(|e| {
            Box::new(AppError::new(
                "SVC-7001",
                "无法清理旧的 staging 目录",
                e.to_string(),
            ))
        })?;
    }
    std::fs::create_dir_all(&dir)?;

    let weight_name = plan
        .weight
        .file_name()
        .ok_or_else(|| Box::new(AppError::new("SVC-2005", "权重文件名无效", "".to_string())))?;
    let config_name = plan
        .config
        .file_name()
        .ok_or_else(|| Box::new(AppError::new("SVC-2005", "配置文件名无效", "".to_string())))?;

    let weight_target = dir.join(weight_name);
    let config_target = dir.join(config_name);
    std::fs::copy(&plan.weight, &weight_target)?;
    std::fs::copy(&plan.config, &config_target)?;

    let source_sha = sha256_file(&plan.weight)?;
    let copied_sha = sha256_file(&weight_target)?;
    if source_sha != copied_sha {
        let _ = std::fs::remove_dir_all(&dir);
        return Err(Box::new(AppError::new(
            "SVC-2004",
            "复制后校验失败",
            format!("{source_sha} != {copied_sha}"),
        )));
    }

    let bytes = std::fs::metadata(&weight_target)
        .map(|m| m.len())
        .unwrap_or(0);
    let config_sha = sha256_file(&config_target)?;
    Ok(StagedCopy {
        dir,
        weight: weight_target,
        config: config_target,
        weight_sha256: copied_sha,
        config_sha256: config_sha,
        bytes,
    })
}

/// 原子落位：把 staging 目录移动到 imports/<slug>/model/，返回新的权重路径。
pub fn finalize(staged: &StagedCopy, imports_root: &Path, slug: &str) -> AppResult<PathBuf> {
    let target_dir = imports_root.join(slug).join("model");
    if target_dir.exists() {
        std::fs::remove_dir_all(&target_dir)?;
    }
    std::fs::create_dir_all(target_dir.parent().expect("has parent"))?;
    std::fs::rename(&staged.dir, &target_dir).map_err(|e| {
        Box::new(AppError::new(
            "SVC-7001",
            "无法把模型移入受管目录",
            e.to_string(),
        ))
    })?;
    let name = staged
        .weight
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    Ok(target_dir.join(name))
}

/// 文件名 → 步数（`G_2400.pth` → 2400）；解析不出返回 None。
pub fn step_from_name(name: &str) -> Option<u64> {
    let stem = name.rsplit_once('.').map(|(s, _)| s).unwrap_or(name);
    let digits = stem.strip_prefix("G_")?;
    digits.parse::<u64>().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::infra::fs::sha256_bytes;

    fn tmpdir() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("svcupload-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(path: &Path, content: &[u8]) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, content).unwrap();
    }

    #[test]
    fn plan_accepts_one_weight_and_one_config() {
        let dir = tmpdir();
        let weight = dir.join("G_2400.pth");
        let config = dir.join("config.json");
        write(&weight, b"w");
        write(&config, b"{}");
        let image = dir.join("cover.png");
        write(&image, b"x");

        let plan = plan_upload(&[weight.clone(), config.clone(), image.clone()]).expect("plan");
        assert_eq!(plan.weight, weight);
        assert_eq!(plan.config, config);
        assert_eq!(plan.ignored, vec![image]);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn plan_rejects_missing_half_with_readable_code() {
        let dir = tmpdir();
        let weight = dir.join("G_1.pth");
        write(&weight, b"w");
        let err = plan_upload(std::slice::from_ref(&weight)).unwrap_err();
        assert_eq!(err.code, "SVC-6008");
        assert!(err.message.contains("请上传模型和配置文件"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn plan_rejects_two_models() {
        let dir = tmpdir();
        let a = dir.join("a.pth");
        let b = dir.join("b.pth");
        let c = dir.join("config.json");
        for p in [&a, &b, &c] {
            write(p, b"x");
        }
        let err = plan_upload(&[a, b, c]).unwrap_err();
        assert!(err.cause.contains("一次只能导入一个模型"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn stage_copy_verifies_and_keeps_source() {
        let dir = tmpdir();
        let staging = dir.join("staging");
        let weight = dir.join("G_2400.pth");
        let config = dir.join("config.json");
        write(&weight, b"weights");
        write(&config, b"{\"model\":{}}");

        let plan = plan_upload(&[weight.clone(), config.clone()]).unwrap();
        let staged = stage_copy(&plan, &staging, "m-abc").expect("stage");
        assert!(staged.weight.is_file());
        assert_eq!(staged.bytes, 7);
        assert!(weight.is_file(), "源文件必须还在");
        assert_eq!(staged.weight_sha256, sha256_bytes(b"weights"));

        let finals = dir.join("imports");
        let new_path = finalize(&staged, &finals, "m-abc").expect("finalize");
        assert!(new_path.is_file());
        assert!(!staging.join("m-abc").exists(), "staging 应被移走");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn slug_is_filesystem_safe() {
        assert_eq!(slug_for("小满 音色", "abcdef1234567890"), "model-abcdef12");
        assert!(slug_for("my voice", "abcdef1234567890").starts_with("my-voice-"));
    }

    #[test]
    fn step_parsing_matches_expected_names() {
        assert_eq!(step_from_name("G_2400.pth"), Some(2400));
        assert_eq!(step_from_name("release.pth"), None);
        assert_eq!(step_from_name("G_x.pth"), None);
    }
}
