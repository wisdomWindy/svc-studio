//! 主进程 ↔ worker 的 JSON Lines 协议（spec §7）。
//!
//! 上行：worker 每次写一行 JSON；下行：主进程写 `{"cmd":"stop"}` / `{"cmd":"cancel"}`。
//! 解析必须容忍未知字段（上游库输出可能带额外键），但 `kind` 必须可识别。

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum WorkerEvent {
    Hello {
        #[serde(default)]
        protocol: u32,
        #[serde(default)]
        bridge: Option<String>,
        #[serde(default)]
        job_id: Option<String>,
    },
    Stage {
        stage: String,
    },
    #[serde(rename_all = "camelCase")]
    Progress {
        #[serde(default)]
        stage: Option<String>,
        #[serde(default)]
        step: i64,
        #[serde(default)]
        total: i64,
        /// 每步的 loss 列表（上游顺序），用于曲线；未知字段一律容忍。
        #[serde(default)]
        losses: Vec<f64>,
        #[serde(default)]
        lr: Option<f64>,
        /// 本次尝试已耗时（秒）：界面用它与 step 算 ETA / 每步耗时。
        #[serde(default)]
        elapsed: Option<f64>,
        /// 当前显存占用（MB，nvidia-smi 采样；读不到时为 None）。
        #[serde(default)]
        vram_mb: Option<u32>,
        /// 本次尝试的显存峰值（MB）。
        #[serde(default)]
        vram_peak_mb: Option<u32>,
    },
    Log {
        #[serde(default = "default_level")]
        level: String,
        message: String,
    },
    Result {
        #[serde(default)]
        data: serde_json::Value,
    },
    Error {
        code: String,
        message: String,
        #[serde(default)]
        cause: Option<String>,
        #[serde(default)]
        retriable: bool,
    },
    Cancelled,
    Stopped,
    Pong {
        #[serde(default)]
        job_id: Option<String>,
    },
}

fn default_level() -> String {
    "info".to_string()
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct ControlCommand {
    pub cmd: String,
}

impl ControlCommand {
    pub fn stop() -> Self {
        Self {
            cmd: "stop".to_string(),
        }
    }
    pub fn cancel() -> Self {
        Self {
            cmd: "cancel".to_string(),
        }
    }
    pub fn ping() -> Self {
        Self {
            cmd: "ping".to_string(),
        }
    }
    pub fn to_line(&self) -> String {
        serde_json::to_string(self).expect("control command is always serializable") + "\n"
    }
}

/// 一行协议文本 → 事件；不合法返回 None（调用方按"stdout 污染"记日志）。
pub fn parse_line(line: &str) -> Option<WorkerEvent> {
    let trimmed = line.trim();
    if trimmed.is_empty() || !trimmed.starts_with('{') {
        return None;
    }
    serde_json::from_str::<WorkerEvent>(trimmed).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_progress_with_and_without_stage() {
        let a = parse_line(r#"{"kind":"progress","stage":"train","step":10,"total":100}"#).unwrap();
        assert_eq!(
            a,
            WorkerEvent::Progress {
                stage: Some("train".into()),
                step: 10,
                total: 100,
                losses: Vec::new(),
                lr: None,
                elapsed: None,
                vram_mb: None,
                vram_peak_mb: None,
            }
        );
        let b = parse_line(r#"{"kind":"progress","step":1,"total":2}"#).unwrap();
        assert_eq!(
            b,
            WorkerEvent::Progress {
                stage: None,
                step: 1,
                total: 2,
                losses: Vec::new(),
                lr: None,
                elapsed: None,
                vram_mb: None,
                vram_peak_mb: None,
            }
        );
    }

    #[test]
    fn parses_progress_telemetry() {
        // 训练遥测：耗时与显存，缺字段时必须是 None 而不是解析失败
        let e = parse_line(
            r#"{"kind":"progress","step":1200,"total":4000,"elapsed":512.5,"vramMb":5830,"vramPeakMb":6102}"#,
        )
        .unwrap();
        match e {
            WorkerEvent::Progress {
                elapsed,
                vram_mb,
                vram_peak_mb,
                ..
            } => {
                assert_eq!(elapsed, Some(512.5));
                assert_eq!(vram_mb, Some(5830));
                assert_eq!(vram_peak_mb, Some(6102));
            }
            other => panic!("应解析为 Progress，实际 {other:?}"),
        }
    }

    #[test]
    fn parses_error_with_optional_fields() {
        let e = parse_line(r#"{"kind":"error","code":"SVC-5001","message":"oom"}"#).unwrap();
        assert_eq!(
            e,
            WorkerEvent::Error {
                code: "SVC-5001".into(),
                message: "oom".into(),
                cause: None,
                retriable: false
            }
        );
    }

    #[test]
    fn tolerates_unknown_fields() {
        let e = parse_line(r#"{"kind":"log","level":"info","message":"x","extra":1}"#);
        assert!(e.is_some());
    }

    #[test]
    fn rejects_non_json_and_garbage() {
        assert!(parse_line("Loading speech encoder...").is_none());
        assert!(parse_line("").is_none());
        assert!(parse_line(r#"{"kind":"unknown_kind"}"#).is_none());
    }

    #[test]
    fn control_commands_are_single_line_json() {
        let line = ControlCommand::stop().to_line();
        assert!(line.ends_with('\n'));
        assert_eq!(line.matches('\n').count(), 1);
        assert_eq!(line.trim(), r#"{"cmd":"stop"}"#);
    }
}
