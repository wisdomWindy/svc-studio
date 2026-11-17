pub mod assets;
pub mod bootstrap;
pub mod dataset;
pub mod dataset_import;
pub mod datasets;
pub mod diagnostics;
pub mod doctor;
pub mod inference;
pub mod job_retry;
pub mod jobs;
pub mod model_tools;
pub mod models;
pub mod perf;
pub mod preprocess;
pub mod projects;
pub mod runner;
pub mod runtime_setup;
pub mod settings;
pub mod train;
#[cfg(test)]
mod spawn_guard {
    /// 同步 `#[tauri::command]` 里调 `tokio::spawn` 会 panic：
    /// "there is no reactor running"，在窗口子系统下表现为程序直接消失。
    /// 必须用 `tauri::async_runtime::spawn`。
    #[test]
    fn start_commands_do_not_use_tokio_spawn() {
        for (name, src) in [
            ("preprocess", include_str!("preprocess.rs")),
            ("train", include_str!("train.rs")),
            ("inference", include_str!("inference.rs")),
        ] {
            assert!(
                !src.contains("tokio::spawn"),
                "{name}.rs 仍在使用 tokio::spawn：同步 command 会 panic，应改为 tauri::async_runtime::spawn"
            );
        }
    }
}
