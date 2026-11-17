//! 诊断包（B5）：白名单打包，绝不包含音频、特征或模型权重。
//!
//! 做法不是"禁止某几个扩展名"，而是**只允许白名单文件名**；未知内容一律跳过并记录。

use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{AppError, AppResult};

pub const MAX_LOG_LINES: usize = 2_000;
pub const MAX_LOG_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PackageReport {
    pub path: PathBuf,
    pub entries: Vec<String>,
    pub skipped: Vec<String>,
    pub bytes: u64,
}

#[derive(Debug, Clone, Default)]
pub struct DiagnosticsInput {
    pub doctor_json: Option<String>,
    pub versions_json: Option<String>,
    pub config_snapshot: Option<String>,
    pub manifest_check: Option<String>,
    pub log_tail: Option<String>,
    /// 允许的附加白名单条目（相对名, 文本内容）。
    pub extras: Vec<(String, String)>,
}

const ALLOWED_NAMES: &[&str] = &[
    "doctor.json",
    "versions.json",
    "config-snapshot.json",
    "manifest-check.json",
    "logs/tail.txt",
];

/// 白名单校验：只允许固定文件名，或 `extras/` 下的纯文本条目（txt/json/log）。
pub fn is_allowed(entry: &str) -> bool {
    let normalized = entry.replace('\\', "/");
    if ALLOWED_NAMES.contains(&normalized.as_str()) {
        return true;
    }
    match normalized.strip_prefix("extras/") {
        Some(rest) => {
            !rest.is_empty()
                && (rest.ends_with(".txt") || rest.ends_with(".json") || rest.ends_with(".log"))
        }
        None => false,
    }
}

pub fn tail_lines(text: &str, max_lines: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    let mut out = lines[start..].join("\n");
    if out.len() > MAX_LOG_BYTES {
        // 按字节截断可能切断 UTF-8，退到最近的换行。
        let mut cut = MAX_LOG_BYTES;
        while cut > 0 && !out.is_char_boundary(cut) {
            cut -= 1;
        }
        out.truncate(cut);
        if let Some(pos) = out.rfind('\n') {
            out.truncate(pos);
        }
    }
    out
}

pub fn build(out_path: &Path, input: &DiagnosticsInput) -> AppResult<PackageReport> {
    if let Some(parent) = out_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = std::fs::File::create(out_path)?;
    let mut zip = zip::ZipWriter::new(file);
    let options: zip::write::SimpleFileOptions = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    let mut entries = Vec::new();
    let mut skipped = Vec::new();

    let mut push =
        |name: &str, content: &str, zip: &mut zip::ZipWriter<std::fs::File>| -> AppResult<()> {
            if !is_allowed(name) {
                skipped.push(name.to_string());
                return Ok(());
            }
            zip.start_file(name, options).map_err(|e| {
                Box::new(AppError::new(
                    "SVC-7001",
                    "写入诊断包失败",
                    format!("{name}: {e}"),
                ))
            })?;
            zip.write_all(content.as_bytes())?;
            entries.push(name.to_string());
            Ok(())
        };

    if let Some(v) = &input.doctor_json {
        push("doctor.json", v, &mut zip)?;
    }
    if let Some(v) = &input.versions_json {
        push("versions.json", v, &mut zip)?;
    }
    if let Some(v) = &input.config_snapshot {
        push("config-snapshot.json", v, &mut zip)?;
    }
    if let Some(v) = &input.manifest_check {
        push("manifest-check.json", v, &mut zip)?;
    }
    if let Some(v) = &input.log_tail {
        push("logs/tail.txt", &tail_lines(v, MAX_LOG_LINES), &mut zip)?;
    }
    for (name, content) in &input.extras {
        push(&format!("extras/{name}"), content, &mut zip)?;
    }

    zip.finish()
        .map_err(|e| Box::new(AppError::new("SVC-7001", "诊断包收尾失败", e.to_string())))?;

    let bytes = std::fs::metadata(out_path)?.len();
    Ok(PackageReport {
        path: out_path.to_path_buf(),
        entries,
        skipped,
        bytes,
    })
}

/// 独立于打包的兜底检查：读取 zip 的所有条目名，任何音频/权重特征即失败。
pub fn audit_package(path: &Path) -> AppResult<Vec<String>> {
    let file = std::fs::File::open(path)?;
    let mut archive = zip::ZipArchive::new(file)
        .map_err(|e| Box::new(AppError::new("SVC-7001", "诊断包无法打开", e.to_string())))?;
    let mut names = Vec::new();
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| Box::new(AppError::new("SVC-7001", "诊断包条目损坏", e.to_string())))?;
        let name = entry.name().to_string();
        if !is_allowed(&name) {
            return Err(Box::new(AppError::new(
                "SVC-7001",
                "诊断包含非白名单文件",
                name,
            )));
        }
        if entry.size() > 0 && entry.size() < 4 * 1024 * 1024 {
            let mut head = [0u8; 4];
            if entry.read_exact(&mut head).is_ok() && &head == b"RIFF" {
                return Err(Box::new(AppError::new(
                    "SVC-7001",
                    "诊断包含有 WAV 数据",
                    name,
                )));
            }
        }
        names.push(name);
    }
    Ok(names)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("svcdiag-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn builds_whitelisted_package_and_skips_unknown() {
        let dir = tmp();
        let out = dir.join("diag.zip");
        let input = DiagnosticsInput {
            doctor_json: Some("{\"schema\":\"x\"}".into()),
            versions_json: Some("{}".into()),
            log_tail: Some("line1\nline2".into()),
            extras: vec![("dataset.wav".to_string(), "RIFFxxxx".to_string())],
            ..Default::default()
        };
        let report = build(&out, &input).expect("build");
        assert!(report.entries.contains(&"doctor.json".to_string()));
        assert!(report.entries.contains(&"logs/tail.txt".to_string()));
        assert!(report.skipped.contains(&"extras/dataset.wav".to_string()));

        let names = audit_package(&out).expect("audit");
        assert_eq!(names.len(), 3);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn tail_keeps_last_lines_and_respects_utf8() {
        let text = (0..10)
            .map(|i| format!("第{i}行"))
            .collect::<Vec<_>>()
            .join("\n");
        let tail = tail_lines(&text, 3);
        assert_eq!(tail.lines().count(), 3);
        assert!(tail.ends_with("第9行"));
    }

    #[test]
    fn audit_rejects_audio_disguised_as_text() {
        let dir = tmp();
        let out = dir.join("bad.zip");
        // 绕过 build 的白名单，直接塞一个 .wav 进去，audit 必须拒绝
        {
            let file = std::fs::File::create(&out).unwrap();
            let mut zip = zip::ZipWriter::new(file);
            zip.start_file("payload.wav", zip::write::SimpleFileOptions::default())
                .unwrap();
            zip.write_all(b"RIFF....WAVE").unwrap();
            zip.finish().unwrap();
        }
        let err = audit_package(&out).unwrap_err();
        assert_eq!(err.code, "SVC-7001");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn allowed_names_are_exact() {
        assert!(is_allowed("doctor.json"));
        assert!(is_allowed("logs/tail.txt"));
        assert!(!is_allowed("logs/../../secret.txt"));
        assert!(!is_allowed("model.pth"));
        assert!(!is_allowed("a.wav"));
    }
}
