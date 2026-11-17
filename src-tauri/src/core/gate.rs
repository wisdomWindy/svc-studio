//! 训练/预处理的环境门禁。
//!
//! 规则（B1/B2）：doctor 未跑过 → 拒绝；doctor 跑过但有阻断项 → 拒绝并列出。
//! 只做"能不能开始"，不替代 doctor 本身；失败文案必须能直接行动。

use std::sync::Mutex;

use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DoctorGate {
    pub trainable: bool,
    pub blocking: Vec<String>,
}

pub fn require_trainable(gate: Option<&DoctorGate>) -> AppResult<()> {
    match gate {
        None => Err(Box::new(AppError::new(
            "SVC-5001",
            "还没有做环境自检",
            "打开设置页运行「环境自检」；通过后才能开始训练或预处理",
        ))),
        Some(g) if !g.trainable => Err(Box::new(AppError::new(
            "SVC-5001",
            "环境自检未通过",
            format!(
                "阻断项：{}。请在设置页按提示修复后重新自检",
                if g.blocking.is_empty() {
                    "未知".to_string()
                } else {
                    g.blocking.join("、")
                }
            ),
        ))),
        Some(_) => Ok(()),
    }
}

/// 从缓存槽位取结论并校验；命令层只需一行调用。
pub fn require_from(slot: &Mutex<Option<DoctorGate>>) -> AppResult<()> {
    let gate = slot.lock().ok().and_then(|g| g.clone());
    require_trainable(gate.as_ref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_when_doctor_never_ran() {
        let err = require_trainable(None).unwrap_err();
        assert_eq!(err.code, "SVC-5001");
        assert!(err.cause.contains("环境自检"));
    }

    #[test]
    fn rejects_when_blocking_items_exist() {
        let gate = DoctorGate {
            trainable: false,
            blocking: vec!["cuda".into(), "encoder".into()],
        };
        let err = require_trainable(Some(&gate)).unwrap_err();
        assert_eq!(err.code, "SVC-5001");
        assert!(err.cause.contains("cuda"));
        assert!(err.cause.contains("encoder"));
    }

    #[test]
    fn require_from_reads_the_shared_slot() {
        let slot = Mutex::new(None);
        assert!(require_from(&slot).is_err(), "未自检必须拒绝");
        *slot.lock().unwrap() = Some(DoctorGate {
            trainable: true,
            blocking: vec![],
        });
        assert!(require_from(&slot).is_ok(), "通过后放行");
    }

    #[test]
    fn allows_when_trainable() {
        let gate = DoctorGate {
            trainable: true,
            blocking: vec![],
        };
        assert!(require_trainable(Some(&gate)).is_ok());
    }
}
