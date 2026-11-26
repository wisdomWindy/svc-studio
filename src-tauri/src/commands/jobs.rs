use serde::Serialize;
use std::path::{Path, PathBuf};

use rusqlite::OptionalExtension;
use tauri::State;

use crate::core::job_state::JobStatus;
use crate::core::models::{Job, JobEvent, JobStatusCount};
use crate::core::repo::jobs;
use crate::error::{AppError, AppResult};
use crate::infra::db::now_iso;
use crate::AppPaths;

/// 任务列表被前端高频轮询（2.5s 一次）。同步命令会在主线程执行，
/// 一旦 SQLite 被写锁占住就会把整个界面卡死（表现为「未响应」），因此放到阻塞线程池。
#[tauri::command]
pub async fn list_jobs(state: State<'_, AppPaths>, limit: Option<i64>) -> AppResult<Vec<Job>> {
    let limit = limit.unwrap_or(100).clamp(1, 500);
    let db = state.db.clone();
    tauri::async_runtime::spawn_blocking(move || db.with(|c| jobs::list_recent(c, limit)))
        .await
        .map_err(|e| Box::new(AppError::new("SVC-7003", "任务列表读取失败", e.to_string())))?
}

#[tauri::command]
pub fn get_job(state: State<'_, AppPaths>, id: String) -> AppResult<Option<Job>> {
    state.db.with(|c| jobs::get(c, &id))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobEventPage {
    pub events: Vec<JobEvent>,
    pub next_seq: i64,
}

#[tauri::command]
pub async fn job_events(
    state: State<'_, AppPaths>,
    job_id: String,
    after_seq: Option<i64>,
    limit: Option<i64>,
) -> AppResult<JobEventPage> {
    let after = after_seq.unwrap_or(0);
    let limit = limit.unwrap_or(500).clamp(1, 2000);
    let db = state.db.clone();
    let job = job_id.clone();
    let events = tauri::async_runtime::spawn_blocking(move || {
        db.with(|c| jobs::list_events(c, &job, after, limit))
    })
    .await
    .map_err(|e| Box::new(AppError::new("SVC-7003", "事件读取失败", e.to_string())))??;
    let next_seq = events.last().map(|e| e.seq).unwrap_or(after);
    Ok(JobEventPage { events, next_seq })
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobsSummary {
    pub counts: Vec<JobStatusCount>,
    pub running: i64,
}

#[tauri::command]
pub fn jobs_summary(state: State<'_, AppPaths>) -> AppResult<JobsSummary> {
    let counts = state.db.with(jobs::counts_by_status)?;
    let running = counts
        .iter()
        .filter(|c| c.status == "running" || c.status == "stopping")
        .map(|c| c.count)
        .sum();
    Ok(JobsSummary { counts, running })
}

/// 排队中的任务直接取消；运行中的任务先终止进程树，再落状态。
#[tauri::command]
pub async fn cancel_job(state: State<'_, AppPaths>, id: String) -> AppResult<Job> {
    let now = now_iso();
    let job = state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "任务不存在", id.clone())))?;

    match JobStatus::parse(&job.status) {
        Some(JobStatus::Queued) | Some(JobStatus::Blocked) => {
            state
                .db
                .with(|c| jobs::set_status(c, &id, "cancelled", None, &now))?;
        }
        Some(JobStatus::Running) | Some(JobStatus::Stopping) => {
            if state.runner.contains(&id) {
                crate::commands::runner::stop_running(state.inner(), &id, true).await?;
            } else {
                // 进程句柄不在本进程（例如上次运行遗留），按对账语义处理。
                state
                    .db
                    .with(|c| jobs::set_status(c, &id, "interrupted", None, &now))?;
            }
        }
        _ => {
            return Err(Box::new(AppError::new(
                "SVC-7002",
                "该任务已结束，无需取消",
                format!("status={}", job.status),
            )));
        }
    }

    state
        .db
        .with(|c| jobs::get(c, &id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7003", "取消后读不回任务", id)))
}

/// 打开该任务自己的推理批次文件夹（资源管理器）。
#[tauri::command]
pub fn job_open_folder(state: State<'_, AppPaths>, id: String) -> AppResult<String> {
    let job = load_job(&state, &id)?;
    let batch = batch_dir_of(&state, &job)?;
    let dir = batch.join("outputs");
    let target = if dir.is_dir() { dir } else { batch };
    if !target.is_dir() {
        return Err(Box::new(AppError::new(
            "SVC-6001",
            "推理产物已不存在",
            target.display().to_string(),
        )));
    }
    crate::commands::projects::open_in_explorer(&target)?;
    Ok(target.to_string_lossy().to_string())
}

fn load_job(state: &AppPaths, id: &str) -> AppResult<Job> {
    state
        .db
        .with(|c| jobs::get(c, id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7002", "任务不存在", id.to_string())))
}

/// 该任务自己的推理批次目录：`<项目>/outputs/<YYYYMMDD-HHMM>_<批次名>`。
///
/// 删除任务时会连这批产物一起删掉，所以定位必须精确：只依据「这个任务自己」的记录，
/// 绝不按修改时间挑最新批次——那样删旧任务会连累别的批次。解析顺序：
/// 1. `inference_runs.report_path`：worker 真实落盘的报告路径，父目录即批次目录，
///    历史任务即使没关联项目也能定位；
/// 2. `job.project_id` + payload 的 `batchName` + `created_at` 推出的时间戳目录；
/// 3. `outputs` 下唯一一个以 `_<批次名>` 结尾的目录；多于一个就报错，不猜。
fn batch_dir_of(state: &AppPaths, job: &Job) -> AppResult<PathBuf> {
    let report_path: Option<String> = state.db.with(|c| {
        Ok(c.query_row(
            "SELECT report_path FROM inference_runs \
             WHERE job_id = ?1 AND report_path IS NOT NULL \
             ORDER BY created_at DESC LIMIT 1",
            [&job.id],
            |r| r.get::<_, String>(0),
        )
        .optional()?)
    })?;
    if let Some(dir) = report_path.and_then(|p| batch_dir_from_report(Path::new(&p))) {
        return Ok(dir);
    }

    let batch_name = batch_name_of(job).ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7002",
            "这条推理任务没有记录批次",
            format!(
                "job={} 缺少 inference_runs 记录与 payload.batchName",
                job.id
            ),
        ))
    })?;
    let project_id = job.project_id.clone().ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7002",
            "这条推理任务没有关联项目",
            format!(
                "job={} 既没有 inference_runs.report_path，也没有 project_id",
                job.id
            ),
        ))
    })?;
    let project = state
        .db
        .with(|c| crate::core::repo::projects::get(c, &project_id))?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "项目不存在", project_id.clone())))?;

    let outputs = PathBuf::from(&project.root_path).join("outputs");
    if let Some(stamp) = batch_stamp(&job.created_at) {
        // 拼出来的名字要走 safe_join，避免 payload 里的批次名带出目录穿越
        if let Ok(candidate) =
            crate::infra::fs::safe_join(&outputs, &format!("{stamp}_{batch_name}"))
        {
            if candidate.is_dir() {
                return Ok(candidate);
            }
        }
    }
    sole_batch_candidate(&outputs, &batch_name).ok_or_else(|| {
        Box::new(AppError::new(
            "SVC-7002",
            "找不到这条任务的推理产物目录",
            format!("job={} outputs={}", job.id, outputs.display()),
        ))
    })
}

/// 从 `report.json` 路径还原批次目录，只认 `<…>/outputs/<批次>/report.json` 这种结构，
/// 避免库里存了脏路径时删到别的目录。
fn batch_dir_from_report(path: &Path) -> Option<PathBuf> {
    let is_report = path
        .file_name()
        .map(|n| n.eq_ignore_ascii_case("report.json"))
        .unwrap_or(false);
    if !is_report {
        return None;
    }
    let batch = path.parent()?;
    let under_outputs = batch
        .parent()
        .and_then(Path::file_name)
        .map(|n| n.eq_ignore_ascii_case("outputs"))
        .unwrap_or(false);
    under_outputs.then(|| batch.to_path_buf())
}

/// 任务 payload 里记录的批次名（推理任务创建时写入的 `batchName`）。
fn batch_name_of(job: &Job) -> Option<String> {
    let payload: serde_json::Value = serde_json::from_str(&job.payload_json).ok()?;
    let name = payload.get("batchName")?.as_str()?.trim().to_string();
    (!name.is_empty()).then_some(name)
}

/// worker 用本地时间 `%Y%m%d-%H%M` 给批次目录打时间戳前缀；由任务的创建时间反推。
fn batch_stamp(created_at: &str) -> Option<String> {
    chrono::DateTime::parse_from_rfc3339(created_at)
        .ok()
        .map(|dt| {
            dt.with_timezone(&chrono::Local)
                .format("%Y%m%d-%H%M")
                .to_string()
        })
}

/// 在 `outputs` 下按「以 `_<批次名>` 结尾」找唯一候选；多个候选就返回 None（不按最新猜）。
fn sole_batch_candidate(outputs: &Path, batch_name: &str) -> Option<PathBuf> {
    let suffix = format!("_{batch_name}");
    let mut found: Vec<PathBuf> = std::fs::read_dir(outputs)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.ends_with(&suffix))
                .unwrap_or(false)
        })
        .collect();
    (found.len() == 1).then(|| found.remove(0))
}

#[tauri::command]
pub fn job_open_output(state: State<'_, AppPaths>, id: String) -> AppResult<String> {
    let job = load_job(&state, &id)?;
    let batch = batch_dir_of(&state, &job)?;

    let out_dir = batch.join("outputs");
    if !out_dir.is_dir() {
        return Err(Box::new(AppError::new(
            "SVC-6001",
            "推理产物已不存在",
            out_dir.display().to_string(),
        )));
    }
    let files: Vec<PathBuf> = std::fs::read_dir(&out_dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.is_file())
                .collect()
        })
        .unwrap_or_default();

    // 优先用默认播放器打开第一个音频；没有音频才退回打开文件夹。
    const AUDIO_EXT: &[&str] = &["wav", "flac", "mp3", "m4a", "ogg", "opus", "aac"];
    let audio = files.iter().find(|p| {
        p.extension()
            .and_then(|e| e.to_str())
            .map(|e| AUDIO_EXT.contains(&e.to_ascii_lowercase().as_str()))
            .unwrap_or(false)
    });

    let target = audio.cloned().unwrap_or_else(|| out_dir.clone());
    let mut cmd = if audio.is_some() {
        // rundll32 走文件关联，能正确处理中文与括号路径
        let mut cmd = std::process::Command::new("rundll32.exe");
        crate::core::proc::hide_console(&mut cmd);
        cmd.arg("url.dll,FileProtocolHandler");
        cmd.arg(&target);
        cmd
    } else {
        let mut cmd = std::process::Command::new("explorer.exe");
        crate::core::proc::hide_console(&mut cmd);
        cmd.arg(&target);
        cmd
    };
    cmd.spawn()
        .map_err(|e| Box::new(AppError::new("SVC-6001", "无法打开产物", e.to_string())))?;
    Ok(target.to_string_lossy().to_string())
}

/// 批量删除任务记录。运行中/排队中的任务不允许删除（返回被拒绝的 id）。
///
/// 推理任务连同它自己的批次产物目录一起删除：先删目录再删记录，产物删不掉（被占用等）
/// 就保留记录并把 id 放进 `outputFailed`，避免删了记录却留下没人管的目录。
/// 定位不到产物目录时（历史任务没有批次记录）只删记录，不再按「最新批次」猜。
#[tauri::command]
pub fn delete_jobs(state: State<'_, AppPaths>, ids: Vec<String>) -> AppResult<serde_json::Value> {
    let mut deleted = 0usize;
    let mut refused: Vec<String> = Vec::new();
    let mut output_failed: Vec<String> = Vec::new();

    for id in &ids {
        let job = state.db.with(|c| jobs::get(c, id))?;
        let Some(job) = job else { continue };
        if job.status == "running" || job.status == "queued" {
            refused.push(id.clone());
            continue;
        }
        if job.kind == "inference" {
            if let Ok(batch) = batch_dir_of(&state, &job) {
                if batch.is_dir() && std::fs::remove_dir_all(&batch).is_err() {
                    output_failed.push(id.clone());
                    continue;
                }
            }
        }
        // 先让运行中的进程登记表释放，再删库
        let _ = state.running.cancel(id);
        state.db.with(|c| jobs::delete(c, id))?;
        deleted += 1;
    }

    Ok(serde_json::json!({
        "deleted": deleted,
        "refused": refused,
        "outputFailed": output_failed,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job_with(payload: &str, project_id: Option<&str>, created_at: &str) -> Job {
        Job {
            id: "job_t".into(),
            kind: "inference".into(),
            project_id: project_id.map(str::to_string),
            payload_json: payload.into(),
            status: "succeeded".into(),
            progress: 1.0,
            step: None,
            total: None,
            error_code: None,
            pid: None,
            started_at: None,
            finished_at: None,
            parent_job_id: None,
            created_at: created_at.into(),
        }
    }

    #[test]
    fn report_path_marks_the_batch_dir() {
        // 与 worker 的落盘结构一致：<项目>/outputs/<时间戳>_<批次名>/report.json
        let dir = batch_dir_from_report(Path::new(
            "D:/d/projects/p/outputs/20261124-2132_batch/report.json",
        ))
        .expect("应认作批次目录");
        assert!(dir.ends_with("20261124-2132_batch"));
        assert_eq!(dir.parent().unwrap().file_name().unwrap(), "outputs");
    }

    #[test]
    fn report_path_outside_outputs_is_rejected() {
        for bad in [
            "D:/d/projects/p/outputs/report.json", // 少了批次这一层
            "D:/d/projects/p/20261124-2132_batch/report.json", // 不在 outputs 下
            "D:/d/projects/p/outputs/20261124-2132_batch/x.json",
        ] {
            assert!(batch_dir_from_report(Path::new(bad)).is_none(), "{bad}");
        }
    }

    #[test]
    fn batch_name_comes_from_payload() {
        let ok = job_with(
            r#"{"batchName":"mytest","count":1}"#,
            Some("prj_1"),
            "2026-11-24T13:32:34.203Z",
        );
        assert_eq!(batch_name_of(&ok).as_deref(), Some("mytest"));
        assert!(batch_name_of(&job_with(r#"{"count":1}"#, None, "")).is_none());
        assert!(batch_name_of(&job_with(r#"{"batchName":"   "}"#, None, "")).is_none());
        assert!(batch_name_of(&job_with("not json", None, "")).is_none());
    }

    #[test]
    fn batch_stamp_is_local_minutes() {
        let stamp = batch_stamp("2026-11-24T13:32:34.203Z").expect("合法时间应有时间戳");
        assert_eq!(stamp.len(), 13, "应为 YYYYMMDD-HHMM：{stamp}");
        assert_eq!(&stamp[8..9], "-");
        assert!(stamp[..8].chars().all(|c| c.is_ascii_digit()));
        assert!(stamp[9..].chars().all(|c| c.is_ascii_digit()));
        assert!(batch_stamp("not a time").is_none());
    }

    #[test]
    fn sole_candidate_needs_exactly_one_match() {
        let dir = tempfile::tempdir().unwrap();
        let outputs = dir.path();
        std::fs::create_dir(outputs.join("20261124-2132_batch")).unwrap();
        std::fs::create_dir(outputs.join("20261124-2135_other")).unwrap();
        assert!(sole_batch_candidate(outputs, "batch")
            .expect("只有一个命中")
            .ends_with("20261124-2132_batch"));

        std::fs::create_dir(outputs.join("20261125-0900_batch")).unwrap();
        assert!(
            sole_batch_candidate(outputs, "batch").is_none(),
            "两个命中时必须拒绝猜"
        );
        assert!(sole_batch_candidate(outputs, "no_such").is_none());
    }
}
