//! 进程宿主：spawn worker、逐行解析事件、支持 stop/cancel、超时后杀整棵进程树。

use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::bridge::protocol::{parse_line, ControlCommand, WorkerEvent};
use crate::error::{AppError, AppResult};

pub const EVENT_CHANNEL_CAPACITY: usize = 1024;
const MAX_LOG_CHARS: usize = 2_000;

#[derive(Debug, Clone)]
pub struct SpawnSpec {
    pub job_id: String,
    pub program: PathBuf,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub cwd: Option<PathBuf>,
}

impl SpawnSpec {
    pub fn new(job_id: impl Into<String>, program: impl Into<PathBuf>) -> Self {
        Self {
            job_id: job_id.into(),
            program: program.into(),
            args: Vec::new(),
            env: Vec::new(),
            cwd: None,
        }
    }

    pub fn arg(mut self, value: impl Into<String>) -> Self {
        self.args.push(value.into());
        self
    }

    pub fn env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn cwd(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cwd = Some(dir.into());
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExitOutcome {
    pub code: Option<i32>,
    pub killed: bool,
}

pub struct RunningJob {
    pub job_id: String,
    pub pid: u32,
    child: Child,
    stdin: ChildStdin,
    rx: mpsc::Receiver<WorkerEvent>,
    readers: Vec<JoinHandle<()>>,
    job: Option<crate::infra::job_object::JobGuard>,
}

impl RunningJob {
    pub fn events(&mut self) -> &mut mpsc::Receiver<WorkerEvent> {
        &mut self.rx
    }

    pub async fn send(&mut self, cmd: ControlCommand) -> AppResult<()> {
        self.stdin
            .write_all(cmd.to_line().as_bytes())
            .await
            .map_err(|e| {
                Box::new(AppError::new(
                    "SVC-7002",
                    "无法向 worker 发送控制指令",
                    e.to_string(),
                ))
            })?;
        self.stdin
            .flush()
            .await
            .map_err(|e| Box::new(AppError::new("SVC-7002", "控制指令写入失败", e.to_string())))?;
        Ok(())
    }

    /// 优雅停止：先发 stop，`grace` 内没退出就终止整棵进程树。
    pub async fn stop(mut self, grace: Duration) -> AppResult<ExitOutcome> {
        let _ = self.send(ControlCommand::stop()).await;
        self.wait_or_kill(grace).await
    }

    pub async fn cancel(mut self, grace: Duration) -> AppResult<ExitOutcome> {
        let _ = self.send(ControlCommand::cancel()).await;
        self.wait_or_kill(grace).await
    }

    async fn wait_or_kill(&mut self, grace: Duration) -> AppResult<ExitOutcome> {
        match tokio::time::timeout(grace, self.child.wait()).await {
            Ok(status) => {
                let status = status?;
                self.finish_readers().await;
                Ok(ExitOutcome {
                    code: status.code(),
                    killed: false,
                })
            }
            Err(_) => {
                self.kill_tree();
                let status = self.child.wait().await?;
                self.finish_readers().await;
                Ok(ExitOutcome {
                    code: status.code(),
                    killed: true,
                })
            }
        }
    }

    pub async fn wait(mut self) -> AppResult<ExitOutcome> {
        let status = self.child.wait().await?;
        self.finish_readers().await;
        Ok(ExitOutcome {
            code: status.code(),
            killed: false,
        })
    }

    fn kill_tree(&mut self) {
        if let Some(job) = &self.job {
            job.terminate();
        }
        let _ = self.child.start_kill();
    }

    async fn finish_readers(&mut self) {
        for handle in self.readers.drain(..) {
            let _ = tokio::time::timeout(Duration::from_secs(2), handle).await;
        }
    }
}

impl Drop for RunningJob {
    fn drop(&mut self) {
        // 兜底：漏掉的句柄不能留下孤儿进程。
        if let Some(job) = &self.job {
            job.terminate();
        }
    }
}

pub fn spawn(spec: SpawnSpec) -> AppResult<RunningJob> {
    let mut cmd = Command::new(&spec.program);
    crate::core::proc::hide_console_async(&mut cmd);
    cmd.args(&spec.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if let Some(dir) = &spec.cwd {
        cmd.current_dir(dir);
    }
    for (k, v) in &spec.env {
        cmd.env(k, v);
    }

    let mut child = cmd.spawn().map_err(|e| {
        Box::new(AppError::new(
            "SVC-1001",
            "无法启动 worker 进程",
            format!("{}: {e}", spec.program.display()),
        ))
    })?;

    let pid = child.id().unwrap_or_default();
    let job = match crate::infra::job_object::JobGuard::new() {
        Ok(guard) => {
            if let Some(handle) = crate::infra::job_object::raw_handle_of(&child) {
                let _ = guard.assign(handle);
            }
            Some(guard)
        }
        Err(_) => None,
    };

    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");
    let stdin = child.stdin.take().expect("stdin piped");
    let (tx, rx) = mpsc::channel(EVENT_CHANNEL_CAPACITY);

    let tx_out = tx.clone();
    let out_reader = tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            match parse_line(&line) {
                Some(event) => {
                    if tx_out.send(event).await.is_err() {
                        break;
                    }
                }
                None => {
                    let truncated: String = line.chars().take(MAX_LOG_CHARS).collect();
                    let event = WorkerEvent::Log {
                        level: "warn".to_string(),
                        message: format!("非协议输出: {truncated}"),
                    };
                    if tx_out.send(event).await.is_err() {
                        break;
                    }
                }
            }
        }
    });

    let err_reader = tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let truncated: String = line.chars().take(MAX_LOG_CHARS).collect();
            let event = WorkerEvent::Log {
                level: "debug".to_string(),
                message: truncated,
            };
            if tx.send(event).await.is_err() {
                break;
            }
        }
    });

    Ok(RunningJob {
        job_id: spec.job_id,
        pid,
        child,
        stdin,
        rx,
        readers: vec![out_reader, err_reader],
        job,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bridge::protocol::WorkerEvent;
    use std::path::PathBuf;
    use std::time::Duration;

    fn python() -> String {
        std::env::var("SVCSTUDIO_PYTHON").unwrap_or_else(|_| "python".to_string())
    }

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("worker")
            .join("tests")
            .join("fixtures")
            .join("fake_worker.py")
    }

    fn spec(scenario: &str) -> SpawnSpec {
        SpawnSpec::new("job_test", python())
            .arg(fixture().to_string_lossy().to_string())
            .arg(scenario)
    }

    async fn next_event(job: &mut RunningJob, timeout: Duration) -> WorkerEvent {
        tokio::time::timeout(timeout, job.events().recv())
            .await
            .expect("event in time")
            .expect("event present")
    }

    #[tokio::test]
    async fn streams_events_and_reports_exit_code() {
        let mut job = spawn(spec("ok")).expect("spawn");
        let mut progress = 0;
        let mut saw_hello = false;
        loop {
            match next_event(&mut job, Duration::from_secs(10)).await {
                WorkerEvent::Hello { .. } => saw_hello = true,
                WorkerEvent::Progress { .. } => progress += 1,
                WorkerEvent::Result { .. } => break,
                _ => {}
            }
        }
        assert!(saw_hello, "必须先收到握手");
        assert_eq!(progress, 3, "三条进度事件都要到");
        let outcome = job.wait().await.expect("wait");
        assert_eq!(outcome.code, Some(0));
        assert!(!outcome.killed);
    }

    #[tokio::test]
    async fn crash_keeps_exit_code_and_does_not_hang() {
        let job = spawn(spec("crash")).expect("spawn");
        let outcome = job.wait().await.expect("wait");
        assert_eq!(outcome.code, Some(3), "崩溃码要原样带回");
    }

    #[tokio::test]
    async fn stop_is_graceful_when_worker_cooperates() {
        let mut job = spawn(spec("echo")).expect("spawn");
        let _hello = next_event(&mut job, Duration::from_secs(10)).await;
        let outcome = job.stop(Duration::from_secs(5)).await.expect("stop");
        assert_eq!(outcome.code, Some(0));
        assert!(!outcome.killed, "合作退出不应被强杀");
    }

    #[tokio::test]
    async fn hang_is_killed_after_grace() {
        let mut job = spawn(spec("hang")).expect("spawn");
        let _hello = next_event(&mut job, Duration::from_secs(10)).await;
        let started = std::time::Instant::now();
        let outcome = job
            .stop(Duration::from_millis(300))
            .await
            .expect("stop returns");
        assert!(outcome.killed, "不响应的 worker 必须被终止");
        assert!(started.elapsed() < Duration::from_secs(10), "不能卡死");
    }

    #[tokio::test]
    async fn flood_of_logs_is_backpressured_not_deadlocked() {
        let mut job = spawn(spec("flood")).expect("spawn");
        let mut logs = 0;
        loop {
            let ev = tokio::time::timeout(Duration::from_secs(20), job.events().recv())
                .await
                .expect("no deadlock");
            match ev {
                Some(WorkerEvent::Log { .. }) => logs += 1,
                Some(WorkerEvent::Result { .. }) => break,
                Some(_) => {}
                None => break,
            }
        }
        assert!(logs >= 2000, "2000 条日志不能丢，实际 {logs}");
        let outcome = job.wait().await.expect("wait");
        assert_eq!(outcome.code, Some(0));
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn killing_job_takes_down_grandchildren() {
        let mut job = spawn(spec("tree")).expect("spawn");
        let mut child_pid = None;
        for _ in 0..5 {
            if let WorkerEvent::Log { message, .. } =
                next_event(&mut job, Duration::from_secs(10)).await
            {
                if let Some(rest) = message.split("child=").nth(1) {
                    child_pid = rest.trim().parse::<u32>().ok();
                    break;
                }
            }
        }
        let child_pid = child_pid.expect("孙进程 pid 应能从日志里读到");
        let outcome = job.stop(Duration::from_millis(300)).await.expect("stop");
        assert!(outcome.killed);

        let mut alive = true;
        for _ in 0..15 {
            tokio::time::sleep(Duration::from_millis(200)).await;
            if !pid_exists(child_pid) {
                alive = false;
                break;
            }
        }
        assert!(!alive, "Job Object 必须连孙进程一起收掉");
    }

    #[cfg(windows)]
    fn pid_exists(pid: u32) -> bool {
        let mut tasklist = std::process::Command::new("tasklist");
        crate::core::proc::hide_console(&mut tasklist);
        let out = tasklist
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output()
            .expect("tasklist");
        String::from_utf8_lossy(&out.stdout).contains(&pid.to_string())
    }
}
