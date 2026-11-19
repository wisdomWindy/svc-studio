use rusqlite::{params, Connection, OptionalExtension};

use crate::core::models::{Job, JobEvent, JobStatusCount};
use crate::error::{AppError, AppResult};

const COLUMNS: &str = "id,type,project_id,payload_json,status,progress,step,total,error_code,pid,started_at,finished_at,parent_job_id,created_at";

fn map(row: &rusqlite::Row<'_>) -> rusqlite::Result<Job> {
    Ok(Job {
        id: row.get(0)?,
        kind: row.get(1)?,
        project_id: row.get(2)?,
        payload_json: row.get(3)?,
        status: row.get(4)?,
        progress: row.get(5)?,
        step: row.get(6)?,
        total: row.get(7)?,
        error_code: row.get(8)?,
        pid: row.get(9)?,
        started_at: row.get(10)?,
        finished_at: row.get(11)?,
        parent_job_id: row.get(12)?,
        created_at: row.get(13)?,
    })
}

pub fn create(
    conn: &Connection,
    id: &str,
    kind: &str,
    project_id: Option<&str>,
    payload_json: &str,
    now: &str,
) -> AppResult<Job> {
    conn.execute(
        "INSERT INTO jobs (id,type,project_id,payload_json,status,progress,created_at)
         VALUES (?1,?2,?3,?4,'queued',0,?5)",
        params![id, kind, project_id, payload_json, now],
    )?;
    get(conn, id)?.ok_or_else(|| Box::new(AppError::new("SVC-7003", "任务写入后读不回来", id)))
}

pub fn get(conn: &Connection, id: &str) -> AppResult<Option<Job>> {
    let sql = format!("SELECT {COLUMNS} FROM jobs WHERE id = ?1");
    Ok(conn.query_row(&sql, [id], map).optional()?)
}

pub fn list_recent(conn: &Connection, limit: i64) -> AppResult<Vec<Job>> {
    let sql = format!("SELECT {COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT ?1");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([limit], map)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// 状态迁移只允许这里改；非法迁移直接报错，避免 UI 把 succeeded 改回 running。
pub fn set_status(
    conn: &Connection,
    id: &str,
    status: &str,
    error_code: Option<&str>,
    now: &str,
) -> AppResult<()> {
    let current = get(conn, id)?
        .ok_or_else(|| Box::new(AppError::new("SVC-7004", "任务不存在", id.to_string())))?;
    if !allowed(&current.status, status) {
        return Err(Box::new(AppError::new(
            "SVC-7002",
            "任务状态迁移非法",
            format!("{} -> {}", current.status, status),
        )));
    }
    let terminal = crate::core::job_state::JobStatus::parse(status)
        .map(|s| s.is_terminal())
        .unwrap_or(false);
    let started = if status == "running" && current.started_at.is_none() {
        Some(now)
    } else {
        current.started_at.as_deref()
    };
    conn.execute(
        "UPDATE jobs SET status=?2, error_code=?3, started_at=?4, finished_at=?5 WHERE id=?1",
        params![
            id,
            status,
            error_code,
            started,
            if terminal { Some(now) } else { None }
        ],
    )?;
    Ok(())
}

fn allowed(from: &str, to: &str) -> bool {
    use crate::core::job_state::JobStatus as S;
    let from = S::parse(from);
    let to = S::parse(to);
    match (from, to) {
        (None, _) | (_, None) => false,
        (Some(a), Some(b)) => a.can_transition_to(b),
    }
}

pub fn set_pid(conn: &Connection, id: &str, pid: u32) -> AppResult<()> {
    conn.execute(
        "UPDATE jobs SET pid = ?2 WHERE id = ?1",
        params![id, i64::from(pid)],
    )?;
    Ok(())
}

pub fn set_progress(
    conn: &Connection,
    id: &str,
    progress: f64,
    step: Option<i64>,
    total: Option<i64>,
) -> AppResult<()> {
    let clamped = progress.clamp(0.0, 1.0);
    conn.execute(
        "UPDATE jobs SET progress = ?2, step = COALESCE(?3, step), total = COALESCE(?4, total) WHERE id = ?1",
        params![id, clamped, step, total],
    )?;
    Ok(())
}

pub struct NewJobEvent<'a> {
    pub level: &'a str,
    pub code: Option<&'a str>,
    pub message: &'a str,
    pub data_json: Option<&'a str>,
    pub ts: &'a str,
}

pub fn append_event(conn: &Connection, job_id: &str, e: &NewJobEvent<'_>) -> AppResult<i64> {
    let next: i64 = conn.query_row(
        "SELECT COALESCE(MAX(seq),0)+1 FROM job_events WHERE job_id = ?1",
        [job_id],
        |r| r.get(0),
    )?;
    conn.execute(
        "INSERT INTO job_events (job_id,seq,ts,level,code,message,data_json) VALUES (?1,?2,?3,?4,?5,?6,?7)",
        params![job_id, next, e.ts, e.level, e.code, e.message, e.data_json],
    )?;
    Ok(next)
}

pub fn list_events(
    conn: &Connection,
    job_id: &str,
    after_seq: i64,
    limit: i64,
) -> AppResult<Vec<JobEvent>> {
    let mut stmt = conn.prepare(
        "SELECT seq,ts,level,code,message,data_json FROM job_events
         WHERE job_id = ?1 AND seq > ?2 ORDER BY seq ASC LIMIT ?3",
    )?;
    let rows = stmt.query_map(params![job_id, after_seq, limit], |row| {
        Ok(JobEvent {
            seq: row.get(0)?,
            ts: row.get(1)?,
            level: row.get(2)?,
            code: row.get(3)?,
            message: row.get(4)?,
            data_json: row.get(5)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

pub fn counts_by_status(conn: &Connection) -> AppResult<Vec<JobStatusCount>> {
    let mut stmt = conn.prepare("SELECT status, count(*) FROM jobs GROUP BY status")?;
    let rows = stmt.query_map([], |r| {
        Ok(JobStatusCount {
            status: r.get(0)?,
            count: r.get(1)?,
        })
    })?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

/// 启动对账：进程句柄已不在的 running/stopping 任务一律标记 interrupted（spec §15）。
pub fn reconcile(conn: &Connection, now: &str) -> AppResult<Vec<String>> {
    let mut stmt =
        conn.prepare("SELECT id,pid FROM jobs WHERE status IN ('running','stopping')")?;
    let stuck: Vec<(String, Option<i64>)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    let mut changed = Vec::new();
    for (id, pid) in stuck {
        if pid.map(process_alive).unwrap_or(false) {
            continue;
        }
        conn.execute(
            "UPDATE jobs SET status='interrupted', finished_at=?2 WHERE id=?1",
            params![id, now],
        )?;
        changed.push(id);
    }
    Ok(changed)
}

#[cfg(windows)]
fn process_alive(pid: i64) -> bool {
    use std::process::Command;
    let mut cmd = Command::new("tasklist");
    crate::core::proc::hide_console(&mut cmd);
    cmd.args(["/FI", &format!("PID eq {pid}"), "/NH"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}

#[cfg(not(windows))]
fn process_alive(pid: i64) -> bool {
    std::path::Path::new(&format!("/proc/{pid}")).exists()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::infra::db::{now_iso, Db};

    fn fixture() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(&dir.path().join("t.db")).unwrap();
        db.migrate().unwrap();
        (dir, db)
    }

    #[test]
    fn lifecycle_and_events() {
        let (_d, db) = fixture();
        db.with(|c| {
            let now = now_iso();
            let j = create(c, "job_1", "train_main", None, "{}", &now)?;
            assert_eq!(j.status, "queued");
            set_status(c, "job_1", "running", None, &now)?;
            set_progress(c, "job_1", 0.5, Some(100), Some(200))?;
            append_event(
                c,
                "job_1",
                &NewJobEvent {
                    level: "info",
                    code: None,
                    message: "step 100",
                    data_json: None,
                    ts: &now,
                },
            )?;
            append_event(
                c,
                "job_1",
                &NewJobEvent {
                    level: "warn",
                    code: Some("SVC-5001"),
                    message: "oom",
                    data_json: None,
                    ts: &now,
                },
            )?;
            let events = list_events(c, "job_1", 0, 10)?;
            assert_eq!(events.len(), 2);
            assert_eq!(events[1].seq, 2);
            assert_eq!(events[1].code.as_deref(), Some("SVC-5001"));
            set_status(c, "job_1", "succeeded", None, &now)?;
            let done = get(c, "job_1")?.unwrap();
            assert_eq!(done.progress, 0.5);
            assert!(done.finished_at.is_some());
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn illegal_transition_is_rejected() {
        let (_d, db) = fixture();
        db.with(|c| {
            let now = now_iso();
            create(c, "job_1", "x", None, "{}", &now)?;
            set_status(c, "job_1", "running", None, &now)?;
            set_status(c, "job_1", "succeeded", None, &now)?;
            let err = set_status(c, "job_1", "running", None, &now).unwrap_err();
            assert_eq!(err.code, "SVC-7002");
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn reconcile_marks_orphans_interrupted() {
        let (_d, db) = fixture();
        db.with(|c| {
            let now = now_iso();
            create(c, "job_a", "train_main", None, "{}", &now)?;
            set_status(c, "job_a", "running", None, &now)?;
            // pid 指向一个几乎不可能存在的进程号
            set_pid(c, "job_a", 4_294_967_000)?;
            let changed = reconcile(c, &now)?;
            assert_eq!(changed, vec!["job_a".to_string()]);
            assert_eq!(get(c, "job_a")?.unwrap().status, "interrupted");
            Ok(())
        })
        .unwrap();
    }
}
