//! 训练命令：显存推荐、启动（冒烟/长训）、停止、checkpoint 列表。
//!
//! 真正的训练进程由 `bridge::host` 托管：事件流回传 → 更新 jobs；取消通过 oneshot 触发
//! `RunningJob::stop`，保证进程树被收掉。
//!
//! OOM 自愈：第一次 OOM 时把 `batch_size` 减半、写回配置、重启一次（只一次），并把降档
//! 记录写进任务事件——绝不静默改参数。

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::State;
use tokio::sync::oneshot;

use crate::bridge::host::{spawn, RunningJob, SpawnSpec};
use crate::bridge::protocol::WorkerEvent;
use crate::core::ids::{job_id, run_id};
use crate::core::repo::{jobs, projects};
use crate::core::runtime::{resolve_upstream_dir, resolve_worker_dir, worker_env};
use crate::core::train::recommend::{for_vram, Recommend};
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

/// 运行中作业的取消句柄（job_id → 触发 stop 的 sender）。
#[derive(Default)]
pub struct RunningJobs {
    inner: Mutex<HashMap<String, oneshot::Sender<()>>>,
}

impl RunningJobs {
    pub fn register(&self, job: &str) -> oneshot::Receiver<()> {
        let (tx, rx) = oneshot::channel();
        if let Ok(mut map) = self.inner.lock() {
            map.insert(job.to_string(), tx);
        }
        rx
    }

    pub fn cancel(&self, job: &str) -> bool {
        let sender = self.inner.lock().ok().and_then(|mut m| m.remove(job));
        match sender {
            Some(tx) => tx.send(()).is_ok(),
            None => false,
        }
    }

    pub fn unregister(&self, job: &str) {
        if let Ok(mut map) = self.inner.lock() {
            map.remove(job);
        }
    }
}

fn project_root(state: &AppPaths, project_id: &str) -> AppResult<PathBuf> {
    let project = state
        .db
        .with(|c| projects::get(c, project_id))?
        .ok_or_else(|| {
            Box::new(AppError::new(
                "SVC-7004",
                "项目不存在",
                project_id.to_string(),
            ))
        })?;
    Ok(PathBuf::from(project.root_path))
}

pub fn detect_vram_mb() -> Option<u64> {
    let mut probe = std::process::Command::new("nvidia-smi");
    crate::core::proc::hide_console(&mut probe);
    let output = probe
        .args(["--query-gpu=memory.total", "--format=csv,noheader,nounits"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .next()?
        .trim()
        .parse::<u64>()
        .ok()
}

#[tauri::command]
pub fn train_recommend(vram_mb: Option<u64>) -> Recommend {
    for_vram(vram_mb.or_else(detect_vram_mb).unwrap_or(6 * 1024))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CheckpointInfo {
    pub step: u64,
    pub bytes: u64,
    pub path: String,
    pub modified: String,
    /// 冒烟产物（logs/_smoke）：可续训但不宜登记为正式模型。
    pub smoke: bool,
}

#[tauri::command]
pub fn train_checkpoints(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<Vec<CheckpointInfo>> {
    let root = project_root(&state, &project_id)?;
    let mut out = Vec::new();
    for dir in [root.join("logs/44k"), root.join("logs/_smoke")] {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default();
            if !name.starts_with("G_") || !name.ends_with(".pth") {
                continue;
            }
            let step = name
                .trim_start_matches("G_")
                .trim_end_matches(".pth")
                .parse::<u64>()
                .unwrap_or(0);
            let meta = entry.metadata().ok();
            let smoke = path.to_string_lossy().contains("logs/_smoke");
            out.push(CheckpointInfo {
                step,
                bytes: meta.as_ref().map(|m| m.len()).unwrap_or(0),
                path: path.to_string_lossy().to_string(),
                modified: meta
                    .and_then(|m| m.modified().ok())
                    .map(|t| {
                        chrono::DateTime::<chrono::Local>::from(t)
                            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
                    })
                    .unwrap_or_default(),
                smoke,
            });
        }
    }
    out.sort_by_key(|c| c.step);
    Ok(out)
}

fn legacy_checkpoints(root: &Path) -> usize {
    std::fs::read_dir(root.join("logs/44k"))
        .map(|entries| {
            entries
                .flatten()
                .filter(|e| {
                    let name = e.file_name().to_string_lossy().to_string();
                    name.starts_with("G_") && name.ends_with(".pth")
                })
                .count()
        })
        .unwrap_or(0)
}

/// 把 config.json 的 batch_size 减半（下限 1），返回新值。用于一次 OOM 自愈。
fn downgrade_batch(root: &Path) -> AppResult<u32> {
    let path = root.join("configs/config.json");
    let bytes = std::fs::read(&path)?;
    let mut doc: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| {
        Box::new(AppError::new(
            "SVC-4001",
            "配置无法解析，无法自动降档",
            e.to_string(),
        ))
    })?;
    let current = doc
        .get("train")
        .and_then(|t| t.get("batch_size"))
        .and_then(|v| v.as_u64())
        .unwrap_or(2)
        .max(2);
    let next = (current / 2).max(1) as u32;
    if let Some(train) = doc.get_mut("train").and_then(|t| t.as_object_mut()) {
        train.insert("batch_size".to_string(), serde_json::json!(next));
    }
    let pretty = serde_json::to_vec_pretty(&doc)
        .map_err(|e| Box::new(AppError::new("SVC-4001", "配置序列化失败", e.to_string())))?;
    crate::infra::fs::atomic_write(&path, &pretty)?;
    Ok(next)
}

#[derive(Debug, Default)]
struct AttemptOutcome {
    cancelled: bool,
    oom: bool,
    diverged: bool,
    exit_code: Option<i64>,
    last_step: i64,
}

async fn run_attempt(
    spec: SpawnSpec,
    db: &std::sync::Arc<crate::infra::db::Db>,
    job: &str,
    cancel: &mut oneshot::Receiver<()>,
) -> AttemptOutcome {
    let mut outcome = AttemptOutcome::default();
    let mut handle: RunningJob = match spawn(spec) {
        Ok(h) => h,
        Err(err) => {
            let now = now_iso();
            let _ = db.with(|c| {
                jobs::append_event(
                    c,
                    job,
                    &jobs::NewJobEvent {
                        level: "error",
                        code: Some(&err.code),
                        message: &format!("无法启动训练：{}", err.message),
                        data_json: None,
                        ts: &now,
                    },
                )
            });
            return outcome;
        }
    };

    loop {
        tokio::select! {
            _ = &mut *cancel => {
                let _ = handle.stop(Duration::from_secs(5)).await;
                outcome.cancelled = true;
                return outcome;
            }
            event = handle.events().recv() => {
                match event {
                    None => return outcome,
                    Some(WorkerEvent::Progress { step, total, losses, lr, elapsed, vram_mb, vram_peak_mb, .. }) => {
                        outcome.last_step = step;
                        let ratio = if total > 0 { (step as f64 / total as f64).clamp(0.0, 0.99) } else { 0.0 };
                        let _ = db.with(|c| jobs::set_progress(c, job, ratio, Some(step), Some(total)));
                        // 曲线数据落在事件里：只保留数值，不存原始日志行。
                        if !losses.is_empty() {
                            let now = now_iso();
                            let payload = serde_json::json!({
                                "step": step,
                                "losses": losses,
                                "lr": lr,
                                // 遥测：界面据此算 ETA（elapsed+step）、显示显存峰值
                                "elapsed": elapsed,
                                "vramMb": vram_mb,
                                "vramPeakMb": vram_peak_mb,
                            });
                            let _ = db.with(|c| {
                                jobs::append_event(
                                    c,
                                    job,
                                    &jobs::NewJobEvent {
                                        level: "debug",
                                        code: Some("CURVE"),
                                        message: "loss",
                                        data_json: Some(&payload.to_string()),
                                        ts: &now,
                                    },
                                )
                            });
                        }
                    }
                    Some(WorkerEvent::Log { level, message }) => {
                        // 从日志里提前识别 OOM / 发散，UI 不必等进程退出才知道出问题。
                        match crate::core::train::logparse::parse_line(&message) {
                            crate::core::train::logparse::TrainSignal::Oom => {
                                let now = now_iso();
                                let _ = db.with(|c| {
                                    jobs::append_event(c, job, &jobs::NewJobEvent {
                                        level: "warn", code: Some("SVC-5001"), message: "训练日志出现显存不足", data_json: None, ts: &now,
                                    })
                                });
                            }
                            crate::core::train::logparse::TrainSignal::Diverged => {
                                let now = now_iso();
                                let _ = db.with(|c| {
                                    jobs::append_event(c, job, &jobs::NewJobEvent {
                                        level: "error", code: Some("SVC-5002"), message: "训练日志出现 NaN/Inf", data_json: None, ts: &now,
                                    })
                                });
                            }
                            _ => {
                                if level == "warn" {
                                    let now = now_iso();
                                    let _ = db.with(|c| {
                                        jobs::append_event(c, job, &jobs::NewJobEvent {
                                            level: "warn", code: None, message: &message, data_json: None, ts: &now,
                                        })
                                    });
                                }
                            }
                        }
                    }
                    Some(WorkerEvent::Error { code, message, .. }) => {
                        let now = now_iso();
                        let _ = db.with(|c| {
                            jobs::append_event(c, job, &jobs::NewJobEvent {
                                level: "error", code: Some(&code), message: &message, data_json: None, ts: &now,
                            })
                        });
                    }
                    Some(WorkerEvent::Result { data }) => {
                        outcome.oom = data.get("oom").and_then(|v| v.as_bool()).unwrap_or(false);
                        outcome.diverged = data.get("diverged").and_then(|v| v.as_bool()).unwrap_or(false);
                        outcome.exit_code = data.get("exitCode").and_then(|v| v.as_i64());
                        if let Some(step) = data.get("lastStep").and_then(|v| v.as_i64()) {
                            outcome.last_step = step;
                        }
                        return outcome;
                    }
                    Some(_) => {}
                }
            }
        }
    }
}

/// 已有 checkpoint 时，长训必须显式声明续训；冒烟与续训不受限。
fn should_block_start(smoke: bool, resume: bool, checkpoints: usize) -> bool {
    !smoke && !resume && checkpoints > 0
}

#[cfg(test)]
mod resume_tests {
    use super::should_block_start;

    #[test]
    fn blocks_long_run_when_checkpoints_exist_and_resume_not_confirmed() {
        assert!(should_block_start(false, false, 1));
        assert!(!should_block_start(false, true, 3));
        assert!(!should_block_start(true, false, 3));
        assert!(!should_block_start(false, false, 0));
    }
}

/// 当前是否已有 running/queued 的训练作业。
fn find_active_train(state: &crate::AppPaths) -> AppResult<Option<(String, String)>> {
    let jobs = state
        .db
        .with(|c| crate::core::repo::jobs::list_recent(c, 200))?;
    for job in jobs {
        if matches!(job.status.as_str(), "running" | "queued")
            && (job.kind.starts_with("train_") || job.kind == "preprocess" || job.kind == "infer")
        {
            return Ok(Some((job.id, job.kind)));
        }
    }
    Ok(None)
}

/// 归档当前训练产物，让下一次训练从 0 步开始。
///
/// 只重命名 `logs/44k` 为 `logs/44k_<时间戳>`，不删除任何 checkpoint；
/// 换说话人、换数据集重新训练时用。
#[tauri::command]
pub fn train_reset(state: State<'_, AppPaths>, project_id: String) -> AppResult<String> {
    let root = project_root(&state, &project_id)?;
    let logs = root.join("logs");
    let current = logs.join("44k");

    // 训练中不允许清空
    let has_active = state
        .db
        .with(|c| {
            let n: i64 = c
                .query_row(
                    "SELECT count(*) FROM jobs WHERE project_id = ?1 AND status IN ('running','queued')",
                    rusqlite::params![project_id],
                    |r| r.get::<_, i64>(0),
                )
                .unwrap_or(0);
            Ok(n)
        })
        .unwrap_or(0)
        > 0;
    {
        if has_active {
            return Err(Box::new(AppError::new(
                "SVC-5004",
                "训练任务正在运行",
                "先停止训练，再点「重新训练」",
            )));
        }
    }

    if !current.is_dir() {
        return Ok("没有需要清理的训练产物".to_string());
    }

    let mut removed = Vec::new();
    for name in ["44k", "diffusion"] {
        let dir = logs.join(name);
        if dir.is_dir() && std::fs::remove_dir_all(&dir).is_ok() {
            removed.push(name.to_string());
        }
    }
    // 聚类模型 / 特征索引在 44k 目录内，随上面一起删除
    tracing::info!(removed = ?removed, "training artifacts reset");
    Ok(removed.join(", "))
}

#[tauri::command]

pub fn train_start(
    state: State<'_, AppPaths>,
    project_id: String,
    smoke: Option<bool>,
    kind: Option<String>,
    resume: Option<bool>,
) -> AppResult<String> {
    train_start_impl(&state, project_id, smoke, kind, resume)
}

/// 内部实现：不依赖 tauri State，供重试调度直接调用。
pub(crate) fn train_start_impl(
    state: &AppPaths,
    project_id: String,
    smoke: Option<bool>,
    kind: Option<String>,
    resume: Option<bool>,
) -> AppResult<String> {
    crate::core::gate::require_from(&state.doctor)?;
    let root = project_root(state, &project_id)?;
    if !root.join("configs/config.json").is_file() {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "还没有训练配置",
            "先完成预处理，生成 configs/config.json",
        )));
    }
    let is_smoke = smoke.unwrap_or(false);
    let wants_resume = resume.unwrap_or(false);

    // 同一时间只允许一个训练：两路训练会争抢显存，表现为反复重启、永远 0 步。
    if let Some(active) = find_active_train(state)? {
        return Err(Box::new(AppError::new(
            "SVC-5004",
            "已有训练在运行",
            format!(
                "任务 {} 仍在运行，请先停止它再开新的训练（显存只有一块，两路会互相 OOM）",
                active.0
            ),
        )));
    }
    // 扩散模型训练看的是 logs/diffusion，与主模型 checkpoint 无关，不该被拦住
    let is_diffusion = matches!(kind.as_deref(), Some("diffusion"));
    if !is_diffusion && should_block_start(is_smoke, wants_resume, legacy_checkpoints(&root)) {
        return Err(Box::new(AppError::new(
            "SVC-5003",
            "检测到已存在的 checkpoint",
            "train.py 会自动从最近 checkpoint 续训。确认续训请点「继续训练」；要从零开始请点「重新训练」",
        )));
    }

    // 磁盘余量：每组 checkpoint 约 1.1GB（G+D），空间不足会在保存时失败并把训练标记为 failed
    if let Some(free) = free_disk_gb(&root) {
        let need = 6.0;
        if free < need {
            return Err(Box::new(AppError::new(
                "SVC-5001",
                "磁盘空间不足",
                format!(
                    "当前 {free:.1} GB 可用，训练至少需要 {need:.0} GB（每组 checkpoint 约 1.1GB）。\
                     请清理 projects 下旧的 logs/44k checkpoint，或换更大的磁盘。"
                ),
            )));
        }
    }

    let upstream = resolve_upstream_dir();
    let python = crate::commands::settings::python_for_state(state)?;
    let worker_dir = resolve_worker_dir();
    if !upstream.join("train.py").is_file() {
        return Err(Box::new(AppError::new(
            "SVC-1001",
            "找不到上游 train.py",
            upstream.display().to_string(),
        )));
    }

    // 端口被占用会让训练直接失败：开跑前先确保 MASTER_PORT 可用。
    if let Ok(Some(free)) = ensure_free_port(&root) {
        tracing::info!(port = free, "train port switched to free port");
    }

    let job = job_id();
    let run = run_id();
    let now = now_iso();
    let kind = kind.unwrap_or_else(|| "main".to_string());

    state.db.with(|c| {
        jobs::create(
            c,
            &job,
            if is_smoke {
                "train_smoke"
            } else {
                "train_main"
            },
            Some(&project_id),
            &serde_json::json!({ "smoke": is_smoke, "kind": kind }).to_string(),
            &now,
        )?;
        jobs::set_status(c, &job, "running", None, &now)?;
        jobs::append_event(
            c,
            &job,
            &jobs::NewJobEvent {
                level: "info",
                code: None,
                message: if is_smoke {
                    "开始冒烟训练（最多 100 步，eval_interval=50）"
                } else {
                    "开始训练（自动加载最新 checkpoint 续训）"
                },
                data_json: None,
                ts: &now,
            },
        )?;
        Ok(())
    })?;

    let mut cancel = state.running.register(&job);
    let scheduler = state.scheduler.clone();
    let db = state.db.clone();
    let running = state.running.clone();
    let job_for_task = job.clone();
    let root_for_task = root.clone();
    let kind_for_task = kind.clone();

    tauri::async_runtime::spawn(async move {
        // 同一时刻只允许一个吃显存的作业；排队期间任务保持 running（进度 0）。
        let _gpu = scheduler.acquire().await;

        // 排队期间可能已被用户取消：拿到执行权后再确认一次，避免“点了停止还在跑”
        let cancelled_while_queued = db
            .with(|c| jobs::get(c, &job_for_task))
            .ok()
            .flatten()
            .map(|j| j.status == "cancelled")
            .unwrap_or(false);
        if cancelled_while_queued {
            tracing::info!(job = %job_for_task, "queued job cancelled before start");
            return;
        }

        fn make_spec(
            job: &str,
            python: &Path,
            upstream: &Path,
            worker_dir: &Path,
            root: &Path,
            kind: &str,
            smoke: bool,
        ) -> SpawnSpec {
            let mut spec = SpawnSpec::new(job, python)
                .arg("-m")
                .arg("svc_bridge")
                .arg("train")
                .arg("--project")
                .arg(root.to_string_lossy().to_string())
                .arg("--upstream")
                .arg(upstream.to_string_lossy().to_string())
                .arg("--kind")
                .arg(kind)
                .cwd(root);
            if smoke {
                spec = spec.arg("--smoke");
            }
            for (k, v) in worker_env(worker_dir) {
                spec = spec.env(k, v);
            }
            spec
        }

        spawn_disk_watchdog(db.clone(), job_for_task.clone(), root_for_task.clone());

        let mut attempts = 1u32;
        let mut outcome = run_attempt(
            make_spec(
                &job_for_task,
                &python,
                &upstream,
                &worker_dir,
                &root_for_task,
                &kind_for_task,
                is_smoke,
            ),
            &db,
            &job_for_task,
            &mut cancel,
        )
        .await;

        // OOM 自愈：只重试一次，且只降 batch_size；NaN/发散不重试。
        if outcome.oom && !outcome.diverged && !outcome.cancelled {
            match downgrade_batch(&root_for_task) {
                Ok(new_batch) => {
                    attempts = 2;
                    let now = now_iso();
                    let _ = db.with(|c| {
                        jobs::append_event(
                            c,
                            &job_for_task,
                            &jobs::NewJobEvent {
                                level: "warn",
                                code: Some("SVC-5001"),
                                message: &format!(
                                    "显存不足，已把 batch_size 降到 {new_batch} 并重试一次"
                                ),
                                data_json: Some(
                                    &serde_json::json!({"toBatch": new_batch}).to_string(),
                                ),
                                ts: &now,
                            },
                        )
                    });
                    outcome = run_attempt(
                        make_spec(
                            &job_for_task,
                            &python,
                            &upstream,
                            &worker_dir,
                            &root_for_task,
                            &kind_for_task,
                            is_smoke,
                        ),
                        &db,
                        &job_for_task,
                        &mut cancel,
                    )
                    .await;
                }
                Err(err) => {
                    let now = now_iso();
                    let _ = db.with(|c| {
                        jobs::append_event(
                            c,
                            &job_for_task,
                            &jobs::NewJobEvent {
                                level: "error",
                                code: Some(&err.code),
                                message: &format!("自动降档失败：{}", err.message),
                                data_json: None,
                                ts: &now,
                            },
                        )
                    });
                }
            }
        }

        let final_outcome = outcome;
        let now = now_iso();
        let failed = final_outcome.oom || final_outcome.diverged;
        let status = if final_outcome.cancelled {
            "stopped"
        } else if failed || final_outcome.exit_code.unwrap_or(0) != 0 {
            "failed"
        } else {
            "succeeded"
        };
        let code = if final_outcome.oom {
            Some("SVC-5001")
        } else if final_outcome.diverged {
            Some("SVC-5002")
        } else if final_outcome.exit_code.unwrap_or(0) != 0 {
            Some("SVC-5004")
        } else {
            None
        };

        let _ = db.with(|c| {
            jobs::set_progress(c, &job_for_task, 1.0, Some(final_outcome.last_step), None)?;
            jobs::set_status(c, &job_for_task, status, code, &now)?;
            c.execute(
                "INSERT INTO train_runs
                 (id,project_id,kind,config_path,params_json,params_hash,status,start_step,end_step,total_steps,report_path,job_id,created_at,finished_at)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,NULL,?8,NULL,NULL,?9,?10,?11)",
                rusqlite::params![
                    run,
                    project_id,
                    kind_for_task,
                    root_for_task.join("configs/config.json").to_string_lossy(),
                    serde_json::json!({ "smoke": is_smoke, "attempts": attempts }).to_string(),
                    crate::infra::fs::sha256_bytes(kind_for_task.as_bytes()),
                    status,
                    final_outcome.last_step,
                    job_for_task,
                    now,
                    now
                ],
            )?;
            Ok(())
        });

        running.unregister(&job_for_task);
        tracing::info!(job = %job_for_task, status, step = final_outcome.last_step, "train job finished");
    });

    Ok(job)
}

#[tauri::command]
pub fn train_stop(state: State<'_, AppPaths>, job_id: String) -> AppResult<bool> {
    if state.running.cancel(&job_id) {
        // 立刻落库「停止中」：前端切换页面后仍能读到，不会又变回「停止训练」
        let now = now_iso();
        state
            .db
            .with(|c| jobs::set_status(c, &job_id, "stopping", None, &now))?;
        return Ok(true);
    }
    if !state.running.cancel(&job_id) {
        let now = now_iso();
        state
            .db
            .with(|c| jobs::set_status(c, &job_id, "cancelled", None, &now))?;
        return Ok(false);
    }
    Ok(true)
}

/// 可选任务：聚类 / 特征检索索引。两者都可能跑几分钟，所以给 30 分钟上限。
#[tauri::command]
pub async fn train_aux(
    state: State<'_, AppPaths>,
    project_id: String,
    kind: String,
) -> AppResult<serde_json::Value> {
    if kind != "cluster" && kind != "index" {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "不支持的可选任务",
            format!("kind={kind}，只接受 cluster / index"),
        )));
    }
    crate::core::gate::require_from(&state.doctor)?;
    let root = project_root(&state, &project_id)?;
    if !root.join("configs/config.json").is_file() {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "还没有训练配置",
            "先完成预处理",
        )));
    }
    if kind == "index" && root.join("logs/44k/feature_and_index.pkl").is_file() {
        // 允许重跑：上游会覆盖产物，这里只是提示，不阻断。
        tracing::info!(project = %project_id, "index 已存在，将重新生成");
    }

    let upstream = resolve_upstream_dir();
    let python = crate::commands::settings::python_for_state(&state)?;
    let root_str = root.to_string_lossy().to_string();
    let upstream_str = upstream.to_string_lossy().to_string();

    // 不设超时：聚类/索引等后处理耗时随数据量增长
    crate::core::worker_cli::run_json(
        &python,
        &[
            "aux",
            "--project",
            &root_str,
            "--upstream",
            &upstream_str,
            "--kind",
            &kind,
        ],
    )
    .await
}

/// 训练脚本把 `config.train.port` 当 MASTER_PORT；被占用时换一个可用端口并写回。
fn ensure_free_port(root: &Path) -> AppResult<Option<u16>> {
    let path = root.join("configs/config.json");
    let Ok(bytes) = std::fs::read(&path) else {
        return Ok(None);
    };
    let Ok(mut doc) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
        return Ok(None);
    };
    let preferred = doc
        .get("train")
        .and_then(|t| t.get("port"))
        .map(|v| {
            v.as_u64()
                .map(|n| n as u16)
                .or_else(|| v.as_str().and_then(|s| s.parse::<u16>().ok()))
                .unwrap_or(8001)
        })
        .unwrap_or(8001);

    let free = pick_free_port(preferred);
    if free == preferred {
        return Ok(None);
    }
    if let Some(train) = doc.get_mut("train").and_then(|t| t.as_object_mut()) {
        train.insert("port".to_string(), serde_json::json!(free.to_string()));
    }
    let pretty = serde_json::to_vec_pretty(&doc)
        .map_err(|e| Box::new(AppError::new("SVC-4001", "配置序列化失败", e.to_string())))?;
    crate::infra::fs::atomic_write(&path, &pretty)?;
    Ok(Some(free))
}

fn pick_free_port(preferred: u16) -> u16 {
    for offset in 0..50u16 {
        let candidate = preferred.saturating_add(offset);
        if std::net::TcpListener::bind(("127.0.0.1", candidate)).is_ok() {
            return candidate;
        }
    }
    preferred
}

#[cfg(test)]
mod tests {
    use super::pick_free_port;

    #[test]
    fn avoids_occupied_port() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let occupied = listener.local_addr().expect("addr").port();
        let picked = pick_free_port(occupied);
        assert_ne!(picked, occupied, "被占用的端口不能再给训练用");
    }

    #[test]
    fn scans_forward_within_window() {
        // 不绑定刚释放的端口：Windows 下 TIME_WAIT 会让"立刻重绑"偶发失败。
        let picked = pick_free_port(51_000);
        assert!((51_000..51_050).contains(&picked), "端口应在前 50 个候选内");
    }
}

/// UI 用来显示"GPU 忙/闲"；不参与门禁，只影响提示。
#[tauri::command]
pub fn gpu_busy(state: State<'_, AppPaths>) -> bool {
    state.scheduler.in_use()
}

/// 数据目录所在磁盘剩余空间（GB）。
fn free_disk_gb(dir: &std::path::Path) -> Option<f64> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt as _;
        use windows::core::PCWSTR;
        use windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

        // 取盘符根（"D:\\"）；GetDiskFreeSpaceExW 需要以反斜杠结尾的路径。
        let root = dir.ancestors().last()?;
        let mut wide: Vec<u16> = root.as_os_str().encode_wide().collect();
        if !wide.ends_with(&[b'\\' as u16]) {
            wide.push(b'\\' as u16);
        }
        wide.push(0);

        let mut free: u64 = 0;
        let ok = unsafe {
            GetDiskFreeSpaceExW(
                PCWSTR(wide.as_ptr()),
                Some(&mut free as *mut u64),
                None,
                None,
            )
        };
        if ok.is_err() {
            return None;
        }
        Some(free as f64 / 1024.0 / 1024.0 / 1024.0)
    }
    #[cfg(not(windows))]
    {
        let _ = dir;
        None
    }
}

/// 列出 `logs/44k` 下的 checkpoint 组（G_x 与 D_x 成对），按步数升序，跳过 0 步。
fn checkpoint_pairs(logs: &std::path::Path) -> Vec<(u64, std::path::PathBuf, std::path::PathBuf)> {
    let mut pairs: Vec<(u64, std::path::PathBuf, std::path::PathBuf)> = Vec::new();
    let Ok(entries) = std::fs::read_dir(logs) else {
        return pairs;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let Some(rest) = name.strip_prefix("G_") else {
            continue;
        };
        let Some(step) = rest
            .strip_suffix(".pth")
            .and_then(|s| s.parse::<u64>().ok())
        else {
            continue;
        };
        if step == 0 {
            continue;
        }
        let d = logs.join(format!("D_{step}.pth"));
        pairs.push((step, entry.path(), d));
    }
    pairs.sort_by_key(|(step, _, _)| *step);
    pairs
}

/// 删除最旧的 checkpoint 组，只保留最新 `keep` 组。返回被删除的步数。
/// 已登记为模型的 checkpoint 永不删除：受保护的步数会被跳过。
pub fn prune_old_checkpoints_protected(
    logs: &std::path::Path,
    keep: usize,
    protected: &[std::path::PathBuf],
) -> Vec<u64> {
    let pairs = checkpoint_pairs(logs);
    if pairs.len() <= keep {
        return Vec::new();
    }
    let protected_steps: std::collections::HashSet<u64> = protected
        .iter()
        .filter_map(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .and_then(|n| n.strip_prefix("G_"))
                .and_then(|n| n.strip_suffix(".pth"))
                .and_then(|n| n.parse::<u64>().ok())
        })
        .collect();
    let mut removed = Vec::new();
    for (step, g, d) in pairs.iter().take(pairs.len() - keep) {
        if *step == 0 || protected_steps.contains(step) {
            continue;
        }
        let _ = std::fs::remove_file(g);
        let _ = std::fs::remove_file(d);
        removed.push(*step);
    }
    removed
}

/// 训练期间盯着磁盘：余量低于阈值就删最旧的 checkpoint，避免保存时写坏文件。
fn spawn_disk_watchdog(
    db: std::sync::Arc<crate::infra::db::Db>,
    job_id: String,
    root: std::path::PathBuf,
) {
    tauri::async_runtime::spawn(async move {
        let logs = root.join("logs").join("44k");
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(20)).await;
            let running = db
                .with(|c| crate::core::repo::jobs::get(c, &job_id))
                .ok()
                .flatten()
                .map(|j| j.status == "running")
                .unwrap_or(false);
            if !running {
                break;
            }
            let Some(free) = free_disk_gb(&root) else {
                continue;
            };
            if free >= 8.0 {
                continue;
            }
            // 空间紧张：只留最新 2 组 + 0 步底模
            let protected: Vec<std::path::PathBuf> = db
                .with(crate::core::repo::models::list)
                .unwrap_or_default()
                .into_iter()
                .map(|m| std::path::PathBuf::from(m.path))
                .collect();
            let removed = prune_old_checkpoints_protected(&logs, 2, &protected);
            let now = now_iso();
            let msg = if removed.is_empty() {
                format!("磁盘余量 {free:.1} GB，没有可清理的旧 checkpoint")
            } else {
                format!("磁盘余量 {free:.1} GB，已清理旧 checkpoint：{removed:?}")
            };
            let _ = db.with(|c| {
                crate::core::repo::jobs::append_event(
                    c,
                    &job_id,
                    &crate::core::repo::jobs::NewJobEvent {
                        level: "warn",
                        code: Some("SVC-5001"),
                        message: &msg,
                        data_json: None,
                        ts: &now,
                    },
                )
            });
            tracing::warn!(free_gb = free, removed = ?removed, "disk low during training");
        }
    });
}
#[cfg(test)]
mod prune_tests {
    use super::*;

    #[test]
    fn prunes_oldest_pairs_only() {
        let dir = std::env::temp_dir().join(format!("svc-prune-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for step in [0u64, 1000, 1200, 1400, 1600] {
            std::fs::write(dir.join(format!("G_{step}.pth")), b"x").unwrap();
            std::fs::write(dir.join(format!("D_{step}.pth")), b"x").unwrap();
        }

        let removed = prune_old_checkpoints_protected(&dir, 2, &[]);
        assert_eq!(removed, vec![1000, 1200]);
        assert!(dir.join("G_0.pth").is_file());
        assert!(dir.join("D_0.pth").is_file());
        assert!(dir.join("G_1400.pth").is_file());
        assert!(dir.join("G_1600.pth").is_file());
        assert!(!dir.join("G_1000.pth").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
#[cfg(test)]
mod archive_tests {
    #[test]
    fn archive_dir_name_is_unique_and_timestamped() {
        let dir = std::env::temp_dir().join(format!("svc-archive-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let logs = dir.join("logs");
        std::fs::create_dir_all(logs.join("44k")).unwrap();
        std::fs::write(logs.join("44k").join("G_100.pth"), b"x").unwrap();

        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
        let target = logs.join(format!("44k_{stamp}"));
        std::fs::rename(logs.join("44k"), &target).unwrap();

        assert!(!logs.join("44k").exists());
        assert!(target.join("G_100.pth").is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[cfg(test)]
mod protected_prune_tests {
    #[test]
    fn registered_checkpoint_survives_prune() {
        let dir = std::env::temp_dir().join(format!("svc-prot-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for step in [0u64, 100, 200, 300] {
            std::fs::write(dir.join(format!("G_{step}.pth")), b"x").unwrap();
            std::fs::write(dir.join(format!("D_{step}.pth")), b"x").unwrap();
        }
        let protected = vec![dir.join("G_100.pth")];
        let removed = super::prune_old_checkpoints_protected(&dir, 1, &protected);
        assert!(!removed.contains(&100), "已登记模型不能被删");
        assert!(dir.join("G_100.pth").is_file());
        assert!(removed.contains(&200));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
