//! 真实进程链路：worker CLI → 事件流 → 停止。
//!
//! 这里不经过 Tauri State，直接驱动 `bridge::host`，验证"进程能起、事件能收、
//! 到冒烟上限能自己停、外部 stop() 能杀干净"。上游 train.py 用假脚本，
//! 因此不依赖 torch。

#![cfg(windows)]

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use super::host::{spawn, SpawnSpec};
use crate::bridge::protocol::WorkerEvent;

fn python() -> String {
    std::env::var("SVCSTUDIO_PYTHON").unwrap_or_else(|_| "python".to_string())
}

fn worker_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("worker")
}

/// 慢速假 train：让 stop() 有足够窗口杀掉进程（fixture 的 ok 模式 0.3s 就跑完）。
const SLOW_TRAIN: &str = r#"
import sys, time
steps = 100000
for i in range(1, steps + 1):
    print(f"Losses: [1.0, 2.0, 0.5, 1.5, 0.9], step: {i}, lr: 0.0001", flush=True)
    time.sleep(0.02)
"#;

fn temp_project() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("svc-e2e-{}", uuid::Uuid::now_v7().simple()));
    std::fs::create_dir_all(dir.join("configs")).unwrap();
    std::fs::create_dir_all(dir.join("upstream")).unwrap();
    std::fs::write(dir.join("configs/config.json"), "{}").unwrap();
    std::fs::write(dir.join("upstream/train.py"), SLOW_TRAIN).unwrap();
    dir
}

fn spec(project: &Path, smoke_steps: Option<i64>) -> SpawnSpec {
    let mut s = SpawnSpec::new("job_e2e", python())
        .arg("-m")
        .arg("svc_bridge")
        .arg("train")
        .arg("--project")
        .arg(project.to_string_lossy().to_string())
        .arg("--upstream")
        .arg(project.join("upstream").to_string_lossy().to_string())
        .env("PYTHONPATH", worker_dir().to_string_lossy().to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .env("SVCSTUDIO_FAKE_MODE", "ok")
        .cwd(project);
    if let Some(steps) = smoke_steps {
        s = s.arg("--smoke").arg("--smoke-steps").arg(steps.to_string());
    }
    s
}

/// 收事件直到拿到 result 或超时；返回 (最大步数, 是否看到 result, 事件种类集合)。
async fn drain(job: &mut super::host::RunningJob, timeout: Duration) -> (i64, bool, Vec<String>) {
    let deadline = Instant::now() + timeout;
    let mut max_step = 0i64;
    let mut saw_result = false;
    let mut kinds = Vec::new();
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            break;
        }
        match tokio::time::timeout(left, job.events().recv()).await {
            Err(_) => break,
            Ok(None) => break,
            Ok(Some(event)) => {
                let kind = match &event {
                    WorkerEvent::Stage { .. } => "stage",
                    WorkerEvent::Progress { .. } => "progress",
                    WorkerEvent::Log { .. } => "log",
                    WorkerEvent::Result { .. } => "result",
                    WorkerEvent::Error { .. } => "error",
                    _ => "other",
                };
                if !kinds.iter().any(|k| k == kind) {
                    kinds.push(kind.to_string());
                }
                match event {
                    WorkerEvent::Progress { step, .. } => {
                        max_step = max_step.max(step);
                    }
                    WorkerEvent::Result { .. } => {
                        saw_result = true;
                        break;
                    }
                    _ => {}
                }
            }
        }
    }
    (max_step, saw_result, kinds)
}

#[tokio::test]
async fn smoke_run_reaches_limit_and_reports_result() {
    let project = temp_project();
    let mut job = spawn(spec(&project, Some(5))).expect("spawn worker");
    let (max_step, saw_result, kinds) = drain(&mut job, Duration::from_secs(30)).await;
    let outcome = job.stop(Duration::from_secs(5)).await.expect("stop");

    assert!(saw_result, "必须收到 result 事件");
    assert!(max_step >= 5, "至少跑到 5 步，实际 {max_step}");
    assert!(kinds.contains(&"progress".to_string()));
    assert!(!outcome.killed, "正常到上限不应被强杀");
    std::fs::remove_dir_all(&project).ok();
}

#[tokio::test]
async fn stop_kills_runner_within_grace() {
    let project = temp_project();
    let mut job = spawn(spec(&project, Some(100_000))).expect("spawn worker");

    // 等到第一条进度，确保进程真的在跑。
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut started = false;
    while Instant::now() < deadline {
        match tokio::time::timeout(Duration::from_secs(5), job.events().recv()).await {
            Ok(Some(WorkerEvent::Progress { .. })) => {
                started = true;
                break;
            }
            Ok(Some(_)) => continue,
            _ => break,
        }
    }
    assert!(started, "未收到进度事件，训练进程可能没起来");

    let began = Instant::now();
    let outcome = job.stop(Duration::from_secs(5)).await.expect("stop");
    let elapsed = began.elapsed();
    assert!(outcome.killed, "长训应当被终止而不是自然退出");
    assert!(elapsed < Duration::from_secs(10), "停止耗时 {:?}", elapsed);
    std::fs::remove_dir_all(&project).ok();
}
