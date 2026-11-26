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
    match serde_json::from_str::<Value>(stdout.trim()) {
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
    let value: Value = serde_json::from_str(stdout.trim()).map_err(|e| {
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

/// 可取消的一次性 CLI：取消时杀掉整棵进程树。返回 (value, exit_code, cancelled)。
pub async fn run_cli_cancellable(
    args: &[&str],
    cancel: tokio::sync::oneshot::Receiver<()>,
) -> AppResult<(Value, Option<i32>, bool)> {
    use std::process::Stdio;

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

    let child = cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker",
            format!("{}: {e}", python.display()),
        ))
    })?;
    let pid = child.id();

    tokio::select! {
        out = child.wait_with_output() => {
            let output = out.map_err(|e| {
                Box::new(AppError::new("SVC-1001", "worker 运行失败", e.to_string()))
            })?;
            let stdout = String::from_utf8_lossy(&output.stdout).to_string();
            let value: Value = serde_json::from_str(stdout.trim()).map_err(|e| {
                Box::new(AppError::new(
                    "SVC-1001",
                    "worker 输出无法解析",
                    format!("{e}; stderr={}", String::from_utf8_lossy(&output.stderr)),
                ))
            })?;
            Ok((value, output.status.code(), false))
        }
        _ = cancel => {
            if let Some(pid) = pid {
                crate::core::proc::kill_tree(pid);
            }
            Ok((Value::Null, None, true))
        }
    }
}
