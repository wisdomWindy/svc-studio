//! 一次性 worker CLI 调用（scan/qc/slice/resample 等）：输入参数、输出单个 JSON。
//!
//! 与进程宿主（`bridge::host`）的区别：这些命令是短任务，不需要事件流，也不需要中途取消；
//! 用一次性进程 + 超时即可，避免把长任务的复杂度带进来。

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;
use tokio::process::Command;

use crate::core::runtime::{resolve_python, resolve_worker_dir, worker_env};
use crate::error::{AppError, AppResult};

pub const CLI_TIMEOUT: Duration = Duration::from_secs(120);

pub async fn run_json(python: &std::path::Path, args: &[&str]) -> AppResult<Value> {
    run_json_with(python, &resolve_worker_dir(), args, CLI_TIMEOUT).await
}

pub async fn run_json_with(
    python: &Path,
    worker_dir: &Path,
    args: &[&str],
    timeout: Duration,
) -> AppResult<Value> {
    let mut cmd = Command::new(python);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args(args);
    for (k, v) in worker_env(worker_dir) {
        cmd.env(k, v);
    }

    let output = tokio::time::timeout(timeout, cmd.output())
        .await
        .map_err(|_| {
            Box::new(AppError::new(
                "SVC-1001",
                "worker 命令超时",
                format!("{}（{}s）", args.join(" "), timeout.as_secs()),
            ))
        })?
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-1001",
                "无法启动 worker",
                format!("{}: {e}", python.display()),
            ))
        })?;

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

pub async fn run_cli(args: &[&str]) -> AppResult<(Value, Option<i32>)> {
    let python = resolve_python();
    let worker = resolve_worker_dir();
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "svc_bridge"]);
    cmd.args(args);
    for (k, v) in worker_env(&worker) {
        cmd.env(k, v);
    }
    let output = tokio::time::timeout(CLI_TIMEOUT, cmd.output())
        .await
        .map_err(|_| Box::new(AppError::new("SVC-1001", "worker 命令超时", args.join(" "))))?
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-1001",
                "无法启动 worker",
                format!("{}: {e}", python.display()),
            ))
        })?;
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
