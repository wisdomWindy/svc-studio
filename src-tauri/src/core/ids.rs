/// 展示名不依赖 UUID 的可读性：`job_` 前缀让日志/数据库一眼可辨。
pub fn job_id() -> String {
    format!("job_{}", uuid::Uuid::now_v7().simple())
}

pub fn project_id() -> String {
    format!("prj_{}", uuid::Uuid::now_v7().simple())
}

pub fn dataset_id() -> String {
    format!("ds_{}", uuid::Uuid::now_v7().simple())
}

pub fn model_id() -> String {
    format!("mdl_{}", uuid::Uuid::now_v7().simple())
}

pub fn run_id() -> String {
    format!("run_{}", uuid::Uuid::now_v7().simple())
}

pub fn event_id() -> String {
    format!("evt_{}", uuid::Uuid::now_v7().simple())
}

/// slug 只允许小写字母、数字与连字符，避免路径与 URL 里的转义问题。
pub fn slugify(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut last_dash = true;
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
            last_dash = false;
        } else if !last_dash {
            out.push('-');
            last_dash = true;
        }
    }
    let trimmed = out.trim_matches('-').to_string();
    if trimmed.is_empty() {
        format!(
            "project-{}",
            &uuid::Uuid::now_v7().simple().to_string()[..8]
        )
    } else {
        trimmed
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique_and_prefixed() {
        let a = job_id();
        let b = job_id();
        assert!(a.starts_with("job_"));
        assert_ne!(a, b);
        assert!(project_id().starts_with("prj_"));
        assert!(model_id().starts_with("mdl_"));
    }

    #[test]
    fn slugify_handles_chinese_and_symbols() {
        assert_eq!(slugify("My Voice 01"), "my-voice-01");
        assert!(slugify("小满的音色").starts_with("project-"));
        assert!(slugify("小满").starts_with("project-"));
        assert!(slugify("---").starts_with("project-"));
        assert_eq!(slugify("A_B/C"), "a-b-c");
    }
}
