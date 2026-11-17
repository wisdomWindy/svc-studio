use std::path::PathBuf;
use std::sync::Mutex;
use std::time::SystemTime;

use rusqlite::Connection;

use crate::error::{AppError, AppResult};

const MIGRATIONS: &[(u32, &str)] = &[(1, include_str!("../../migrations/V001__init.sql"))];

/// 单连接 + Mutex：桌面端并发极小，换来的是事务简单与无连接池状态。
pub struct Db {
    conn: Mutex<Connection>,
    pub path: PathBuf,
    pub version: u32,
}

impl Db {
    pub fn open(path: &std::path::Path) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path).map_err(|e| {
            Box::new(AppError::new(
                "SVC-7003",
                "本地数据库无法打开",
                e.to_string(),
            ))
        })?;

        conn.pragma_update(None, "journal_mode", "WAL").ok();
        conn.pragma_update(None, "synchronous", "NORMAL").ok();
        conn.pragma_update(None, "foreign_keys", "ON").ok();
        conn.busy_timeout(std::time::Duration::from_millis(5_000))
            .ok();

        let version: u32 = conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap_or(0);

        Ok(Self {
            conn: Mutex::new(conn),
            path: path.to_path_buf(),
            version,
        })
    }

    /// 幂等迁移：只跑 `user_version` 之后的脚本；升级前先备份。
    pub fn migrate(&mut self) -> AppResult<u32> {
        let conn = self.conn.get_mut().expect("db poisoned");
        let current: u32 = conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap_or(0);

        for (target, sql) in MIGRATIONS {
            if *target <= current {
                continue;
            }
            if current > 0 && self.path.exists() {
                let backup = self.path.with_extension(format!("db.bak.{current}"));
                std::fs::copy(&self.path, &backup)?;
            }
            let tx = conn.transaction().map_err(|e| {
                Box::new(AppError::new("SVC-7003", "数据库迁移失败", e.to_string()))
            })?;
            tx.execute_batch(sql).map_err(|e| {
                Box::new(AppError::new(
                    "SVC-7003",
                    "数据库迁移脚本执行失败",
                    format!("v{target}: {e}"),
                ))
            })?;
            tx.pragma_update(None, "user_version", *target)
                .map_err(|e| {
                    Box::new(AppError::new(
                        "SVC-7003",
                        "写入 schema 版本失败",
                        e.to_string(),
                    ))
                })?;
            tx.commit().map_err(|e| {
                Box::new(AppError::new(
                    "SVC-7003",
                    "数据库迁移提交失败",
                    e.to_string(),
                ))
            })?;
            self.version = *target;
        }
        self.version = current.max(self.version);
        Ok(self.version)
    }

    pub fn with<T>(&self, f: impl FnOnce(&Connection) -> AppResult<T>) -> AppResult<T> {
        let conn = self.conn.lock().map_err(|_| {
            Box::new(AppError::new(
                "SVC-7003",
                "数据库连接已损坏",
                "mutex poisoned",
            ))
        })?;
        f(&conn)
    }

    pub fn with_mut<T>(&self, f: impl FnOnce(&mut Connection) -> AppResult<T>) -> AppResult<T> {
        let mut conn = self.conn.lock().map_err(|_| {
            Box::new(AppError::new(
                "SVC-7003",
                "数据库连接已损坏",
                "mutex poisoned",
            ))
        })?;
        f(&mut conn)
    }
}

pub fn now_iso() -> String {
    let now = SystemTime::now();
    let dt: chrono::DateTime<chrono::Utc> = now.into();
    dt.to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::OptionalExtension;

    fn temp_db() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().expect("tempdir");
        let db = Db::open(&dir.path().join("studio.db")).expect("open");
        (dir, db)
    }

    #[test]
    fn migrate_creates_schema_and_is_idempotent() {
        let (_dir, mut db) = temp_db();
        assert_eq!(db.migrate().expect("migrate"), 1);
        assert_eq!(db.migrate().expect("migrate again"), 1);

        let tables: i64 = db
            .with(|c| {
                Ok(c.query_row(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
                    [],
                    |r| r.get(0),
                )?)
            })
            .expect("count");
        assert_eq!(tables, 19, "附录 C 的 18 张表 + import_sessions");
    }

    #[test]
    fn foreign_keys_are_enforced() {
        let (_dir, mut db) = temp_db();
        db.migrate().expect("migrate");
        let err = db
            .with(|c| {
                c.execute(
                    "INSERT INTO datasets (id, project_id, version, root_path, created_at) \
                     VALUES ('d1','missing-project',1,'/tmp','2026-01-01T00:00:00Z')",
                    [],
                )?;
                Ok(())
            })
            .expect_err("FK 必须拦住孤儿记录");
        assert!(format!("{err:?}").contains("FOREIGN KEY"));
    }

    #[test]
    fn status_check_rejects_unknown_state() {
        let (_dir, mut db) = temp_db();
        db.migrate().expect("migrate");
        let err = db
            .with(|c| {
                c.execute(
                    "INSERT INTO jobs (id,type,payload_json,status,created_at) \
                     VALUES ('j1','x','{}','zombie','2026-01-01T00:00:00Z')",
                    [],
                )?;
                Ok(())
            })
            .expect_err("非法状态必须被 CHECK 拒绝");
        assert!(format!("{err:?}").contains("CHECK"));
    }

    #[test]
    fn migration_keeps_user_version() {
        let (_dir, mut db) = temp_db();
        db.migrate().expect("migrate");
        let v: Option<i64> = db
            .with(|c| {
                Ok(c.query_row("PRAGMA user_version", [], |r| r.get(0))
                    .optional()?)
            })
            .expect("version");
        assert_eq!(v, Some(1));
    }
}
