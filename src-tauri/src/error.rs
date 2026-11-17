use serde::Serialize;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NextAction {
    pub label: String,
    pub deeplink: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "lowercase")]
#[allow(dead_code)] // 四档严重级是 spec §11 的完整契约，当前只用到两档
pub enum Severity {
    Blocking,
    Recoverable,
    Warning,
    Fatal,
}

/// 错误对象契约见 spec §11：现象 / 原因 / 下一步，前端不得自行拼文案。
/// 用 `Box` 传递，避免 `Result<T, AppError>` 因错误体过大被 clippy 判为大错误。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppError {
    pub code: String,
    pub severity: Severity,
    pub message: String,
    pub cause: String,
    pub next_action: NextAction,
    pub detail: Option<String>,
    pub retriable: bool,
}

impl AppError {
    pub fn new(code: &str, message: impl Into<String>, cause: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            severity: Severity::Fatal,
            message: message.into(),
            cause: cause.into(),
            next_action: NextAction {
                label: "重试".to_string(),
                deeplink: None,
            },
            detail: None,
            retriable: true,
        }
    }

    pub fn io(context: &str, err: std::io::Error) -> Self {
        Self {
            code: "SVC-7001".to_string(),
            severity: Severity::Blocking,
            message: format!("无法访问 {context}"),
            cause: err.to_string(),
            next_action: NextAction {
                label: "更换目录".to_string(),
                deeplink: Some("/settings".to_string()),
            },
            detail: None,
            retriable: false,
        }
    }
}

impl From<std::io::Error> for Box<AppError> {
    fn from(err: std::io::Error) -> Self {
        Box::new(AppError::io("文件系统", err))
    }
}

impl From<rusqlite::Error> for Box<AppError> {
    fn from(err: rusqlite::Error) -> Self {
        Box::new(AppError {
            code: "SVC-7003".to_string(),
            severity: Severity::Fatal,
            message: "数据库操作失败".to_string(),
            cause: err.to_string(),
            next_action: NextAction {
                label: "重试".to_string(),
                deeplink: None,
            },
            detail: None,
            retriable: true,
        })
    }
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {} — {}", self.code, self.message, self.cause)
    }
}

impl std::error::Error for AppError {}

pub type AppResult<T> = Result<T, Box<AppError>>;

/// 数据根：%USERPROFILE%\SVCStudio（项目、模型、资产、日志、临时目录）。
pub fn data_root() -> AppResult<PathBuf> {
    let home = std::env::var("USERPROFILE").map_err(|_| {
        Box::new(AppError::new(
            "SVC-7001",
            "无法确定用户目录",
            "USERPROFILE 未设置",
        ))
    })?;
    let env_value = std::env::var(crate::infra::dataroot::DATA_ROOT_ENV).ok();
    let pointer = crate::infra::dataroot::read_pointer();
    let install_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(std::path::Path::to_path_buf));
    Ok(crate::infra::dataroot::pick_data_root_with(
        env_value.as_deref(),
        pointer.as_deref(),
        &PathBuf::from(home),
        install_dir.as_deref(),
    ))
}
