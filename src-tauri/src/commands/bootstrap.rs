use serde::Serialize;
use tauri::State;

use crate::error::AppResult;
use crate::AppPaths;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Gpu {
    pub name: String,
    pub vram_mb: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Bootstrap {
    pub app_version: String,
    pub runtime_version: Option<String>,
    /// 内置运行时是否就位（安装版应有 resources/runtime/python.exe）
    pub runtime_ready: bool,
    pub python_path: String,
    pub db_version: u32,
    pub data_root: String,
    /// 项目根目录（默认在安装目录下的 projects）。
    pub projects_root: String,
    pub webview2_version: Option<String>,
    pub gpu: Option<Gpu>,
    pub first_run: bool,
    /// 探针采样时长（毫秒），来自 SVCSTUDIO_PROBE_MS，默认 5000。
    pub probe_ms: u64,
    /// 是否要求前端在启动后跑一次主线程响应探针（环境变量 SVCSTUDIO_PROBE=1）
    pub probe: bool,
    pub reconciled_jobs: usize,
}

/// 启动握手：目录与数据库就绪、schema 版本、孤儿任务对账结果。
/// 诊断开关：设 SVCSTUDIO_DOCTOR_ON_START=1 时，启动即在后台跑一次 doctor。
///
/// 用途是验证「worker 子进程不会弹出控制台窗口」——正常情况下前端点「环境自检」
/// 才会拉起 worker，自动化测试里没法点按钮，所以留这个开关。
fn maybe_run_doctor() {
    if std::env::var("SVCSTUDIO_DOCTOR_ON_START").ok().as_deref() != Some("1") {
        return;
    }
    let python = crate::core::runtime::resolve_python();
    if !(python.is_absolute() && python.is_file()) {
        return;
    }
    let worker = crate::core::runtime::resolve_worker_dir();
    let mut cmd = std::process::Command::new(python);
    crate::core::proc::hide_console(&mut cmd);
    cmd.arg("-m")
        .arg("svc_bridge")
        .arg("doctor")
        .arg("--json")
        .current_dir(&worker)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .envs(crate::core::runtime::worker_env(&worker));
    let _ = cmd.spawn();
}

/// 读内置运行时的 Python 版本；失败返回 None（不阻塞启动）。
fn probe_python_version(python: &std::path::Path) -> Option<String> {
    let mut cmd = std::process::Command::new(python);
    crate::core::proc::hide_console(&mut cmd);
    let output = cmd
        .arg("-c")
        .arg("import sys; print(sys.version.split()[0])")
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!text.is_empty()).then_some(text)
}

#[tauri::command]
pub fn bootstrap(state: State<'_, AppPaths>) -> AppResult<Bootstrap> {
    maybe_run_doctor();
    let first_run = !state.data_root.join(".bootstrapped").exists();
    if first_run {
        crate::infra::fs::atomic_write(&state.data_root.join(".bootstrapped"), b"0.1.0\n")?;
    }

    let python = crate::core::runtime::resolve_python();
    let runtime_ready = python.is_absolute() && python.is_file();
    let runtime_version = if runtime_ready {
        probe_python_version(&python)
    } else {
        None
    };

    Ok(Bootstrap {
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        runtime_version,
        runtime_ready,
        python_path: python.to_string_lossy().to_string(),
        db_version: state.db.version,
        data_root: state.data_root.to_string_lossy().to_string(),
        projects_root: {
            let configured = state
                .db
                .with(|c| crate::core::repo::settings::get_raw(c, "projects_root"))
                .ok()
                .flatten()
                .and_then(|raw| serde_json::from_str::<String>(&raw).ok());
            crate::core::projects_root::resolve_projects_root(
                &state.data_root,
                configured.as_deref(),
            )
            .to_string_lossy()
            .to_string()
        },
        webview2_version: None,
        gpu: state
            .gpu
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .map(|(name, vram)| Gpu {
                name,
                vram_mb: vram.max(0) as u64,
            }),
        first_run,
        probe_ms: std::env::var("SVCSTUDIO_PROBE_MS")
            .ok()
            .and_then(|v| v.parse::<u64>().ok())
            .filter(|v| *v >= 1000)
            .unwrap_or(5000),
        probe: std::env::var("SVCSTUDIO_PROBE")
            .map(|v| v == "1")
            .unwrap_or(false),
        reconciled_jobs: state.reconciled.len(),
    })
}

#[tauri::command]
pub fn ping() -> String {
    "pong".to_string()
}
