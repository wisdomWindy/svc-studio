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
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(std::path::Path::to_path_buf));

    // 开发布局（cargo 产物在 target/debug）：仓库根同时有 runtime/ 与 pretrain/ 时，
    // 直接把它当数据根，避免开发时数据跑到 target/debug 里。
    if let Some(dir) = exe_dir.as_deref() {
        if dir
            .file_name()
            .and_then(|n| n.to_str())
            .map(|n| n == "debug" || n == "release")
            == Some(true)
        {
            if let Some(project_root) = dir.parent().and_then(|p| p.parent()) {
                if project_root.join("runtime").join("python.exe").is_file()
                    && project_root.join("pretrain").is_dir()
                {
                    return Ok(project_root.to_path_buf());
                }
            }
        }
    }

    let install_dir = exe_dir;
    let picked = crate::infra::dataroot::pick_data_root_with(
        env_value.as_deref(),
        pointer.as_deref(),
        &PathBuf::from(home),
        install_dir.as_deref(),
    );

    // 一次性迁移：老版本把数据放在安装目录里（卸载会整个删掉）。
    // 若安装目录内还留着 data/，而新的「同级目录」还不存在，直接搬过去（同盘改名，瞬间完成）。
    if let Some(install) = install_dir.as_deref() {
        let legacy = install.join("data");
        let target_missing = !picked.exists()
            || std::fs::read_dir(&picked)
                .map(|mut it| it.next().is_none())
                .unwrap_or(true);
        if legacy.is_dir() && target_missing && picked != legacy {
            if let Some(parent) = picked.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            match std::fs::rename(&legacy, &picked) {
                Ok(()) => tracing::info!(
                    from = %legacy.display(),
                    to = %picked.display(),
                    "已把数据目录迁出安装目录（避免卸载误删）"
                ),
                Err(e) => tracing::warn!("数据目录迁移失败：{e}"),
            }
        }
    }

    Ok(picked)
}
