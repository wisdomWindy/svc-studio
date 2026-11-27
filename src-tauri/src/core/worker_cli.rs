//! 一次性 worker CLI 调用（scan/qc/slice/resample/separate 等）：输入参数、输出单个 JSON。
//!
//! 与进程宿主（`bridge::host`）的区别：这些命令不需要事件流，也不需要中途取消。
//!
//! **不设超时。** 分离、切片、预处理、模型工具的耗时只取决于素材量与机器（CPU/GPU）性能，
//! 任何固定上限都会在慢机器上误杀正常任务——用户看到的「worker 命令超时 SVC-1001」就是这么来的。
//! 需要中断请用 `run_cli_cancellable`（用户主动取消 → 杀进程树），而不是靠时间猜测。

use std::path::{Path, PathBuf};

use serde_json::Value;
use tokio::process::Command;

use crate::core::runtime::{resolve_python, resolve_worker_dir, worker_env};
use crate::error::{AppError, AppResult};

fn command_with_env(python: &Path, worker_dir: &Path, args: &[&str]) -> Command {
    let mut cmd = Command::new(python);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args(args);
    for (k, v) in worker_env(worker_dir) {
        cmd.env(k, v);
    }
    cmd
}

/// 从 worker 的 stdout 里取 JSON。
///
/// 正常情况下整份 stdout 就是一个 JSON 对象；但 worker 的第三方库（torch/librosa）偶尔
/// 会往 stdout 打诊断信息，所以再退一步：从最后一行往前找第一行能解析成 JSON 的内容，
/// 避免一条无关的日志把整个命令判成「输出无法解析」（SVC-1001）。
fn parse_json_output(stdout: &str) -> Result<Value, serde_json::Error> {
    match serde_json::from_str::<Value>(stdout.trim()) {
        Ok(value) => Ok(value),
        Err(first_err) => {
            for line in stdout.lines().rev() {
                let line = line.trim();
                if !(line.starts_with('{') || line.starts_with('[')) {
                    continue;
                }
                if let Ok(value) = serde_json::from_str::<Value>(line) {
                    return Ok(value);
                }
            }
            Err(first_err)
        }
    }
}

/// 等待子进程结束：跑多久等多久。
async fn wait_output(python: &Path, mut cmd: Command) -> AppResult<std::process::Output> {
    cmd.output().await.map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker",
            format!("{}: {e}", python.display()),
        ))
    })
}

pub async fn run_json(python: &Path, args: &[&str]) -> AppResult<Value> {
    let worker_dir = resolve_worker_dir();
    run_json_impl(python, &worker_dir, args).await
}

async fn run_json_impl(python: &Path, worker_dir: &Path, args: &[&str]) -> AppResult<Value> {
    let cmd = command_with_env(python, worker_dir, args);
    let output = wait_output(python, cmd).await?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr: String = String::from_utf8_lossy(&output.stderr)
        .chars()
        .take(2000)
        .collect();

    // qc 在存在阻断项时用非零码退出，但仍返回完整报告：先尝试解析，再看退出码。
    match parse_json_output(&stdout) {
        Ok(value) => Ok(value),
        Err(parse_err) => Err(Box::new(AppError::new(
            "SVC-1001",
            "worker 输出无法解析",
            format!(
                "exit={:?}; parse={parse_err}; stderr={stderr}",
                output.status.code()
            ),
        ))),
    }
}

/// 一次性 CLI，跑多久等多久。
pub async fn run_cli(args: &[&str]) -> AppResult<(Value, Option<i32>)> {
    let python = resolve_python();
    let cmd = command_with_env(&python, &resolve_worker_dir(), args);
    let output = wait_output(&python, cmd).await?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let value: Value = parse_json_output(&stdout).map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "worker 输出无法解析",
            format!("{e}; stderr={}", String::from_utf8_lossy(&output.stderr)),
        ))
    })?;
    Ok((value, output.status.code()))
}

/// 供测试与排障：显式指定解释器与 worker 目录。
fn _worker_paths_unused() -> (PathBuf, PathBuf) {
    (resolve_python(), resolve_worker_dir())
}

/// 探测 worker 是否支持某个开关：跑一次 `<args>`（通常是 `preprocess --help`），
/// 看输出里有没有这个开关。
///
/// 升级应用时不会重新下载 3.6GB 运行时，旧运行时里的 worker 可能还没有新开关
/// （例如 0.3.17 的 `--events`）；这时必须退回旧调用方式，不能让预处理直接
/// 因为 argparse 不认参数而失败。
pub async fn supports_flag(args: &[&str], flag: &str) -> bool {
    use std::process::Stdio;

    let python = resolve_python();
    let worker = resolve_worker_dir();
    let mut cmd = command_with_env(&python, &worker, args);
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    match cmd.output().await {
        Ok(output) => {
            let help = format!(
                "{}{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            help.contains(flag)
        }
        Err(_) => false,
    }
}

/// 事件名：与 worker 的 `rpc.emit_progress` 对应。
fn progress_event(line: &str) -> Option<Value> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    (value.get("kind").and_then(Value::as_str) == Some("progress")).then_some(value)
}

/// 可取消的一次性 CLI，边跑边把 `kind=progress` 事件交给回调。
///
/// worker 以 `--events` 运行时 stdout 是「JSON Lines 协议」：进度事件逐行推送，
/// 最后一条 `kind=result` 带最终结果。这样长任务（预处理 489 个切片要 6 分钟）
/// 才能把进度写进 DB，而不是一直 0% 直到结束。
pub async fn run_cli_cancellable_with_progress(
    args: &[&str],
    cancel: tokio::sync::oneshot::Receiver<()>,
    on_progress: std::sync::Arc<dyn Fn(&Value) + Send + Sync>,
) -> AppResult<(Value, Option<i32>, bool)> {
    use std::{process::Stdio, sync::Arc};

    use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};

    let python = resolve_python();
    let worker = resolve_worker_dir();
    let mut cmd = Command::new(&python);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args(args);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    for (k, v) in worker_env(&worker) {
        cmd.env(k, v);
    }

    let mut child = cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker",
            format!("{}: {e}", python.display()),
        ))
    })?;
    let pid = child.id();
    let stdout = child.stdout.take().ok_or_else(|| {
        Box::new(AppError::new("SVC-1001", "worker 输出不可用", "stdout"))
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        Box::new(AppError::new("SVC-1001", "worker 输出不可用", "stderr"))
    })?;

    let stderr_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        let _ = BufReader::new(stderr).read_to_end(&mut buf).await;
        String::from_utf8_lossy(&buf).to_string()
    });

    let progress = Arc::clone(&on_progress);
    let reader = tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        let mut raw = String::new();
        let mut result: Option<Value> = None;
        while let Ok(Some(line)) = lines.next_line().await {
            if line.trim().is_empty() {
                continue;
            }
            match progress_event(&line) {
                Some(event) => progress(&event),
                None => {
                    let value: Value = match serde_json::from_str(line.trim()) {
                        Ok(value) => value,
                        Err(_) => {
                            raw.push_str(&line);
                            raw.push('\n');
                            continue;
                        }
                    };
                    match value.get("kind").and_then(Value::as_str) {
                        Some("result") => result = value.get("data").cloned(),
                        _ => {
                            raw.push_str(&line);
                            raw.push('\n');
                        }
                    }
                }
            }
        }
        (result, raw)
    });

    tokio::select! {
        status = child.wait() => {
            let status = status.map_err(|e| {
                Box::new(AppError::new("SVC-1001", "worker 运行失败", e.to_string()))
            })?;
            let (result, raw) = reader.await.map_err(|e| {
                Box::new(AppError::new("SVC-1001", "worker 输出读取失败", e.to_string()))
            })?;
            let stderr = stderr_task.await.unwrap_or_default();
            let value = match result {
                Some(value) => value,
                None => parse_json_output(&raw).map_err(|e| {
                    Box::new(AppError::new(
                        "SVC-1001",
                        "worker 输出无法解析",
                        format!("{e}; stderr={stderr}"),
                    ))
                })?,
            };
            Ok((value, status.code(), false))
        }
        _ = cancel => {
            if let Some(pid) = pid {
                crate::core::proc::kill_tree(pid);
            }
            reader.abort();
            stderr_task.abort();
            Ok((Value::Null, None, true))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_json_stdout() {
        let out = r#"{"schema":"svcstudio.dataset.scan/1","fileCount":616}"#;
        let v = parse_json_output(out).expect("纯 JSON 应解析成功");
        assert_eq!(v["fileCount"], 616);
    }

    #[test]
    fn tolerates_log_lines_before_the_json() {
        // 0.3.0 的故障形态：worker 把诊断打到 stdout，JSON 排在后面
        let out = "[separate] RoFormer 实现：bs_roformer_module\n\
                   [separate] RoFormer 配置里这些键被忽略：['flash_attn']\n\
                   {\"schema\":\"svcstudio.dataset.separate/1\",\"ok\":true}\n";
        let v = parse_json_output(out).expect("日志在前也必须能找到 JSON");
        assert_eq!(v["ok"], true);
    }

    #[test]
    fn tolerates_json_wrapped_by_warnings() {
        let out = "Warning: something\n{\"schema\":\"x\",\"ok\":true}\nTraceback tail\n";
        let v = parse_json_output(out).expect("前后都有噪声也要解析成功");
        assert_eq!(v["schema"], "x");
    }

    #[test]
    fn fails_when_there_is_no_json_at_all() {
        let err = parse_json_output("totally not json\nstill not").unwrap_err();
        assert!(format!("{err}").contains("expected value") || format!("{err}").contains("line"));
    }

    #[test]
    fn extracts_progress_events_and_ignores_other_lines() {
        let event = progress_event(
            r#"{"v":1,"kind":"progress","stage":"hubert_f0","step":120,"total":489}"#,
        )
        .expect("进度行应被识别");
        assert_eq!(event["stage"], "hubert_f0");
        assert_eq!(event["step"], 120);
        assert_eq!(event["total"], 489);

        assert!(progress_event(r#"{"kind":"result","data":{}}"#).is_none());
        assert!(progress_event("[separate] 普通日志").is_none());
    }
}
