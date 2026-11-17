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
#[tauri::command]
pub fn bootstrap(state: State<'_, AppPaths>) -> AppResult<Bootstrap> {
    let first_run = !state.data_root.join(".bootstrapped").exists();
    if first_run {
        crate::infra::fs::atomic_write(&state.data_root.join(".bootstrapped"), b"0.1.0\n")?;
    }

    Ok(Bootstrap {
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        runtime_version: None,
        runtime_ready: {
            let python = crate::core::runtime::resolve_python();
            python.is_file()
        },
        python_path: crate::core::runtime::resolve_python()
            .to_string_lossy()
            .to_string(),
        db_version: state.db.version,
        data_root: state.data_root.to_string_lossy().to_string(),
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
