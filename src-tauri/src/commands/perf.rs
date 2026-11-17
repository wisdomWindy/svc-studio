//! 性能探针结果的落盘入口（仅 `SVCSTUDIO_PROBE=1` 启动时由前端调用）。

use serde_json::Value;
use tauri::State;

use crate::error::AppResult;
use crate::infra::db::now_iso;
use crate::AppPaths;

#[tauri::command]
pub fn save_probe_result(state: State<'_, AppPaths>, result: Value) -> AppResult<String> {
    let dir = state.data_root.join("logs").join("perf");
    std::fs::create_dir_all(&dir)?;
    let stamp = now_iso().replace([':', '.'], "-");
    let path = dir.join(format!("probe-{stamp}.json"));
    let body = serde_json::to_vec_pretty(&serde_json::json!({
        "schema": "svcstudio.probe/1",
        "generatedAt": now_iso(),
        "result": result,
        "note": "主线程延迟（setTimeout 偏差），采样期间的后台负载见同一时间的任务/证据记录",
    }))
    .map_err(|e| {
        Box::new(crate::error::AppError::new(
            "SVC-4001",
            "探针结果无法序列化",
            e.to_string(),
        ))
    })?;
    crate::infra::fs::atomic_write(&path, &body)?;
    Ok(path.to_string_lossy().to_string())
}
