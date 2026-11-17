mod bridge;
mod commands;
mod core;
mod error;
mod infra;

use std::path::PathBuf;
use std::sync::Arc;

use tauri::Manager;
use tracing_subscriber::EnvFilter;

use crate::core::runner::Runner;
use crate::core::scheduler::Scheduler;
use crate::infra::db::{now_iso, Db};

/// 首次启动把随包清单放进数据目录（不覆盖用户已导入的版本）。
fn seed_manifest(root: &std::path::Path) {
    let target = root.join("assets").join("manifest.json");
    if target.exists() {
        return;
    }
    let bundled = include_bytes!("../../assets/manifest.json");
    if let Err(err) = crate::infra::fs::atomic_write(&target, bundled) {
        tracing::warn!(%err, "seed manifest failed");
    }
}

pub struct AppPaths {
    pub data_root: PathBuf,
    pub db: Arc<Db>,
    pub runner: Arc<Runner>,
    pub scheduler: Scheduler,
    pub reconciled: Vec<String>,
    pub running: Arc<commands::train::RunningJobs>,
    /// 最近一次环境自检的结论；未跑过为 None。
    pub doctor: Arc<std::sync::Mutex<Option<core::gate::DoctorGate>>>,
    /// 最近一次自检里读到的显卡信息（名称、显存）。
    pub gpu: Arc<std::sync::Mutex<Option<(String, i64)>>>,
}

fn init_state() -> Result<AppPaths, String> {
    let root = error::data_root().map_err(|e| e.to_string())?;
    for dir in ["projects", "models", "assets", "logs", "tmp"] {
        std::fs::create_dir_all(root.join(dir)).map_err(|e| e.to_string())?;
    }
    seed_manifest(&root);
    let mut db = Db::open(&root.join("studio.db")).map_err(|e| e.to_string())?;
    db.migrate().map_err(|e| e.to_string())?;

    let reconciled = db
        .with(|c| core::repo::jobs::reconcile(c, &now_iso()))
        .map_err(|e| e.to_string())?;
    db.with(core::repo::models::mark_missing)
        .map_err(|e| e.to_string())?;

    Ok(AppPaths {
        data_root: root,
        db: Arc::new(db),
        runner: Arc::new(Runner::new()),
        scheduler: Scheduler::new(),
        running: Arc::new(commands::train::RunningJobs::default()),
        doctor: Arc::new(std::sync::Mutex::new(None)),
        gpu: Arc::new(std::sync::Mutex::new(None)),
        reconciled,
    })
}

fn setup_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::TrayIconBuilder;

    let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;

    let mut builder = TrayIconBuilder::with_id("svc-studio-tray")
        .tooltip("SVC Studio")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                }
            }
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// 关闭窗口 = 收进托盘，不杀任务（G4）。真正退出走托盘菜单的"退出"。
fn setup_close_to_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::WindowEvent;

    if let Some(window) = app.get_webview_window("main") {
        let handle = window.clone();
        window.on_window_event(move |event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = handle.hide();
            }
        });
    }
    Ok(())
}

pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(false)
        .compact()
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let state = init_state().map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            tracing::info!(
                db = %state.db.path.display(),
                version = state.db.version,
                reconciled = state.reconciled.len(),
                "state ready"
            );
            app.manage(state);
            setup_tray(app)?;
            setup_close_to_tray(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap::bootstrap,
            commands::bootstrap::ping,
            commands::projects::create_project,
            commands::projects::list_projects,
            commands::projects::get_project,
            commands::projects::delete_project,
            commands::jobs::list_jobs,
            commands::jobs::get_job,
            commands::jobs::job_events,
            commands::jobs::jobs_summary,
            commands::jobs::cancel_job,
            commands::job_retry::retry_job,
            commands::models::list_models,
            commands::models::get_model,
            commands::models::default_model,
            commands::models::set_default_model,
            commands::models::delete_model,
            commands::datasets::list_datasets,
            commands::settings::get_settings,
            commands::settings::set_setting,
            commands::settings::consents_status,
            commands::runtime_setup::runtime_setup_status,
            commands::runtime_setup::install_runtime_from_zip,
            commands::settings::accept_consents,
            commands::assets::assets_manifest,
            commands::assets::import_asset_dir,
            commands::doctor::doctor_report,
            commands::doctor::doctor_cached,
            commands::perf::save_probe_result,
            commands::diagnostics::export_diagnostics,
            commands::dataset::dataset_scan,
            commands::dataset::dataset_qc,
            commands::dataset::dataset_slice,
            commands::dataset::dataset_resample,
            commands::preprocess::preprocess_start,
            commands::preprocess::preprocess_manifest,
            commands::preprocess::dataset_qc_verdict,
            commands::preprocess::config_assert,
            commands::train::train_recommend,
            commands::train::train_start,
            commands::train::train_stop,
            commands::train::train_checkpoints,
            commands::train::train_aux,
            commands::train::gpu_busy,
            commands::models::register_model,
            commands::model_tools::model_compress,
            commands::model_tools::model_onnx,
            commands::model_tools::mix_preview,
            commands::inference::infer_start,
            commands::inference::infer_batches,
            commands::inference::infer_batch_report,
            commands::inference::model_check,
            commands::inference::import_model_upload,
            commands::runner::run_diagnostics
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
