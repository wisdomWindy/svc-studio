//! 预处理命令：启动三步链路（后台作业）、读取 manifest、配置断言。

use std::path::PathBuf;
use std::sync::Arc;

use rusqlite::OptionalExtension;
use serde_json::Value;
use tauri::State;

use crate::core::ids::{job_id, run_id};
use crate::core::repo::{jobs, projects};
use crate::core::runtime::resolve_upstream_dir;
use crate::core::worker_cli::{run_cli, run_cli_with};
use crate::error::{AppError, AppResult};
use crate::infra::db::{now_iso, Db};
use crate::AppPaths;

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

fn latest_dataset(state: &AppPaths, project_id: &str) -> AppResult<String> {
    state.db.with(|c| {
        c.query_row(
            "SELECT id FROM datasets WHERE project_id = ?1 ORDER BY version DESC LIMIT 1",
            [project_id],
            |r| r.get::<_, String>(0),
        )
        .map_err(|_| {
            Box::new(AppError::new(
                "SVC-3002",
                "还没有数据集，先完成质检",
                "preprocess 需要 dataset/44k 的产物",
            ))
        })
    })
}

fn has_active_job(state: &AppPaths, project_id: &str) -> AppResult<bool> {
    state.db.with(|c| {
        let count: i64 = c.query_row(
            "SELECT count(*) FROM jobs WHERE project_id = ?1 AND status IN ('queued','running','stopping')",
            [project_id],
            |r| r.get(0),
        )?;
        Ok(count > 0)
    })
}

fn record_failure(db: &Arc<Db>, job: &str, code: &str, message: &str) {
    let now = now_iso();
    let _ = db.with(|c| {
        jobs::append_event(
            c,
            job,
            &jobs::NewJobEvent {
                level: "error",
                code: Some(code),
                message,
                data_json: None,
                ts: &now,
            },
        )
    });
    let _ = db.with(|c| jobs::set_status(c, job, "failed", Some(code), &now));
}

/// 统计 dataset/44k 下的 wav 数量，用于估算预处理超时。
fn count_dataset_clips(root: &std::path::Path) -> usize {
    fn walk(dir: &std::path::Path, acc: &mut usize) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, acc);
            } else if path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case("wav"))
                .unwrap_or(false)
            {
                *acc += 1;
            }
        }
    }
    let mut count = 0;
    walk(&root.join("dataset").join("44k"), &mut count);
    count
}

#[tauri::command]
pub fn preprocess_start(
    state: State<'_, AppPaths>,
    project_id: String,
    use_diff: Option<bool>,
    vol_aug: Option<bool>,
    num_processes: Option<i64>,
) -> AppResult<String> {
    crate::core::gate::require_from(&state.doctor)?;
    let root = project_root(&state, &project_id)?;
    let dataset_id = latest_dataset(&state, &project_id)?;
    // C5：QC 阻断项未清零不允许进入预处理。
    let verdict = dataset_qc_verdict_inner(&state, &dataset_id)?;
    match verdict {
        None => {
            return Err(Box::new(AppError::new(
                "SVC-3003",
                "数据集还没有质检报告",
                "先在数据集页运行 QC，阻断项清零后再预处理",
            )))
        }
        Some(v) if !v.0 => {
            return Err(Box::new(AppError::new(
                "SVC-3003",
                "数据集质检未通过",
                format!("阻断项 {} 个；先在数据集页修复", v.1),
            )))
        }
        Some(_) => {}
    }
    if has_active_job(&state, &project_id)? {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "该项目还有未结束的任务",
            "等它结束或先取消，避免同时写同一份产物",
        )));
    }

    let project = state
        .db
        .with(|c| projects::get(c, &project_id))?
        .expect("已校验存在");
    let upstream = resolve_upstream_dir();
    if !upstream.join("resample.py").is_file() {
        return Err(Box::new(AppError::new(
            "SVC-1001",
            "找不到上游源码",
            format!(
                "{} 缺少 resample.py（开发期在 vendor/，发布期在 resources/upstream）",
                upstream.display()
            ),
        )));
    }

    let job = job_id();
    let run = run_id();
    let now = now_iso();
    let use_diff = use_diff.unwrap_or(false);
    let vol_aug = vol_aug.unwrap_or(false);
    let procs = num_processes.unwrap_or(1).clamp(1, 8);

    state.db.with(|c| {
        jobs::create(
            c,
            &job,
            "preprocess",
            Some(&project_id),
            &serde_json::json!({
                "useDiff": use_diff,
                "volAug": vol_aug,
                "numProcesses": procs,
                "encoder": project.speech_encoder,
                "f0": project.f0_predictor,
            })
            .to_string(),
            &now,
        )?;
        jobs::set_status(c, &job, "running", None, &now)?;
        jobs::append_event(
            c,
            &job,
            &jobs::NewJobEvent {
                level: "info",
                code: None,
                message: "开始预处理（resample → flist_config → hubert_f0）",
                data_json: None,
                ts: &now,
            },
        )?;
        Ok(())
    })?;

    let scheduler = state.scheduler.clone();
    let db = state.db.clone();
    let manifest_path = root
        .join("reports")
        .join("preprocess_manifest.json")
        .to_string_lossy()
        .to_string();
    let project_for_task = project_id.clone();

    let encoder = project.speech_encoder.clone();
    let f0 = project.f0_predictor.clone();
    let job_for_task = job.clone();
    let run_for_task = run.clone();
    let dataset_for_task = dataset_id.clone();
    let root_for_task = root.clone();

    tauri::async_runtime::spawn(async move {
        let _gpu = scheduler.acquire().await;

        let args = vec![
            "preprocess".to_string(),
            "--project".to_string(),
            root_for_task.to_string_lossy().to_string(),
            "--upstream".to_string(),
            upstream.to_string_lossy().to_string(),
            "--encoder".to_string(),
            encoder,
            "--f0".to_string(),
            f0,
            "--num-processes".to_string(),
            procs.to_string(),
            "--manifest".to_string(),
            manifest_path.clone(),
        ];
        let mut args = args;
        if use_diff {
            args.push("--use-diff".to_string());
        }
        if vol_aug {
            args.push("--vol-aug".to_string());
        }
        let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();

        // 预处理耗时随片段数线性增长：每个片段给 25s，另加 5 分钟固定开销
        let clips = count_dataset_clips(&root_for_task);
        let budget = std::time::Duration::from_secs(300 + 25 * clips.max(1) as u64);
        let result = run_cli_with(&arg_refs, budget).await;
        let now = now_iso();

        match result {
            Ok((value, code)) => {
                let passed = value
                    .get("passed")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                let diffs = value
                    .get("diffs")
                    .and_then(Value::as_array)
                    .map(|a| a.len())
                    .unwrap_or(0);
                let counts = value.get("counts").cloned().unwrap_or(Value::Null);

                let _ = db.with(|c| {
                    c.execute(
                        "INSERT INTO preprocess_runs
                         (id,project_id,dataset_id,speech_encoder,f0_predictor,use_diff,vol_aug,num_processes,status,manifest_path,started_at,finished_at)
                         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)",
                        rusqlite::params![
                            run_for_task,
                            project_for_task,
                            dataset_for_task,
                            value.get("params").and_then(|p| p.get("speechEncoder")).and_then(Value::as_str).unwrap_or(""),
                            value.get("params").and_then(|p| p.get("f0Predictor")).and_then(Value::as_str).unwrap_or(""),
                            i64::from(use_diff),
                            i64::from(vol_aug),
                            procs,
                            if passed { "succeeded" } else { "failed" },
                            manifest_path,
                            now,
                            now
                        ],
                    )?;
                    Ok(())
                });

                let _ = db.with(|c| {
                    jobs::append_event(
                        c,
                        &job_for_task,
                        &jobs::NewJobEvent {
                            level: if passed { "info" } else { "error" },
                            code: if passed { None } else { Some("SVC-4003") },
                            message: &format!("预处理结束：passed={passed}，产物差异 {diffs} 项",),
                            data_json: Some(&counts.to_string()),
                            ts: &now,
                        },
                    )?;
                    jobs::set_progress(c, &job_for_task, 1.0, None, None)?;
                    jobs::set_status(
                        c,
                        &job_for_task,
                        if passed { "succeeded" } else { "failed" },
                        if passed { None } else { Some("SVC-4003") },
                        &now,
                    )?;
                    let _ = code;
                    Ok(())
                });
            }
            Err(err) => {
                record_failure(
                    &db,
                    &job_for_task,
                    &err.code,
                    &format!("预处理失败：{}", err.message),
                );
            }
        }
    });

    Ok(job)
}

#[tauri::command]
pub fn preprocess_manifest(
    state: State<'_, AppPaths>,
    project_id: String,
) -> AppResult<Option<Value>> {
    let root = project_root(&state, &project_id)?;
    let path = root.join("reports").join("preprocess_manifest.json");
    if !path.is_file() {
        return Ok(None);
    }
    let bytes = std::fs::read(&path)?;
    let value = serde_json::from_slice(&bytes).map_err(|e| {
        Box::new(AppError::new(
            "SVC-4003",
            "预处理 manifest 无法解析",
            e.to_string(),
        ))
    })?;
    Ok(Some(value))
}

#[tauri::command]
pub async fn config_assert(state: State<'_, AppPaths>, project_id: String) -> AppResult<Value> {
    let root = project_root(&state, &project_id)?;
    let config = root.join("configs").join("config.json");
    let diffusion = root.join("configs").join("diffusion.yaml");
    if !config.is_file() || !diffusion.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-4001",
            "配置还没生成",
            "先跑一次预处理，或从模板导入 config.json / diffusion.yaml",
        )));
    }
    let dataset = root.join("dataset").join("44k");
    let (value, _code) = run_cli(&[
        "config",
        "assert",
        "--config",
        &config.to_string_lossy(),
        "--diffusion",
        &diffusion.to_string_lossy(),
        "--dataset",
        &dataset.to_string_lossy(),
    ])
    .await?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_wav(path: &std::path::Path, seconds: f64) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        let sr: u32 = 44_100;
        let frames = (sr as f64 * seconds) as usize;
        let samples: Vec<i16> = (0..frames).map(|i| ((i % 200) as i16) * 10).collect();
        let data_len = (samples.len() * 2) as u32;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16u32.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&sr.to_le_bytes());
        bytes.extend_from_slice(&(sr * 2).to_le_bytes());
        bytes.extend_from_slice(&2u16.to_le_bytes());
        bytes.extend_from_slice(&16u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_len.to_le_bytes());
        for s in &samples {
            bytes.extend_from_slice(&s.to_le_bytes());
        }
        std::fs::write(path, bytes).unwrap();
    }

    #[tokio::test]
    async fn config_assert_blocks_duration_longer_than_shortest_clip() {
        let worker = crate::core::runtime::resolve_worker_dir();
        if !worker.join("svc_bridge").is_dir() {
            eprintln!("skip: worker 源码不在预期位置");
            return;
        }
        let base = std::env::temp_dir().join(format!("svccfg-{}", uuid::Uuid::now_v7().simple()));
        let root = base.join("project");
        std::fs::create_dir_all(root.join("configs")).unwrap();
        write_wav(&root.join("dataset/44k/spk/a.wav"), 1.0);

        let config = serde_json::json!({
            "train": {"batch_size": 2, "epochs": 10, "learning_rate": 0.0001,
                      "keep_ckpts": 3, "all_in_mem": false, "fp16_run": true},
            "model": {"vocoder_name": "nsf-hifigan", "ssl_dim": 768, "speech_encoder": "vec768l12"},
            "data": {"sampling_rate": 44100},
            "spk": {"spk": 0}
        });
        std::fs::write(
            root.join("configs/config.json"),
            serde_json::to_vec(&config).unwrap(),
        )
        .unwrap();
        // duration=3s > 最短切片 1s → 必须被 SVC-4002 拦住
        std::fs::write(
            root.join("configs/diffusion.yaml"),
            "data:\n  duration: 3\n",
        )
        .unwrap();

        let (value, _code) = run_cli(&[
            "config",
            "assert",
            "--config",
            &root.join("configs/config.json").to_string_lossy(),
            "--diffusion",
            &root.join("configs/diffusion.yaml").to_string_lossy(),
            "--dataset",
            &root.join("dataset/44k").to_string_lossy(),
        ])
        .await
        .expect("config assert 必须返回结构化结果");

        assert_eq!(value["passed"], serde_json::json!(false));
        let blocking = value["blocking"].as_array().unwrap();
        assert!(blocking
            .iter()
            .any(|b| b["code"] == serde_json::json!("SVC-4002")));
        let _ = std::fs::remove_dir_all(base);
    }
}

/// 最近一次 QC 结论：None 表示从未跑过。
fn dataset_qc_verdict_inner(state: &AppPaths, dataset_id: &str) -> AppResult<Option<(bool, i64)>> {
    state.db.with(|c| {
        let row = c
            .query_row(
                "SELECT passed, blocking_count FROM qc_reports WHERE dataset_id = ?1 ORDER BY generated_at DESC LIMIT 1",
                [dataset_id],
                |r| Ok((r.get::<_, i64>(0)? != 0, r.get::<_, i64>(1)?)),
            )
            .optional()?;
        Ok(row)
    })
}

/// UI 用：读取最新数据集的 QC 结论。
#[tauri::command]
pub fn dataset_qc_verdict(state: State<'_, AppPaths>, project_id: String) -> AppResult<Value> {
    let dataset_id = latest_dataset(&state, &project_id)?;
    let verdict = dataset_qc_verdict_inner(&state, &dataset_id)?;
    Ok(match verdict {
        None => serde_json::json!({ "exists": false, "passed": false, "blocking": 0 }),
        Some((passed, blocking)) => {
            serde_json::json!({ "exists": true, "passed": passed, "blocking": blocking })
        }
    })
}
#[cfg(test)]
mod timeout_budget_tests {
    use super::count_dataset_clips;

    #[test]
    fn clip_count_scales_timeout() {
        let dir = std::env::temp_dir().join(format!("svc-clips-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let spk = dir.join("dataset").join("44k").join("s");
        std::fs::create_dir_all(&spk).unwrap();
        for i in 0..7 {
            std::fs::write(spk.join(format!("a{i}.wav")), b"x").unwrap();
        }
        std::fs::write(spk.join("skip.txt"), b"x").unwrap();

        assert_eq!(count_dataset_clips(&dir), 7);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
