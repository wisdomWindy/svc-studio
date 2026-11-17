use rusqlite::{params, Connection, OptionalExtension};

use crate::error::AppResult;

pub fn get_raw(conn: &Connection, key: &str) -> AppResult<Option<String>> {
    Ok(conn
        .query_row(
            "SELECT value_json FROM settings WHERE key = ?1",
            [key],
            |r| r.get(0),
        )
        .optional()?)
}

pub fn set_raw(conn: &Connection, key: &str, value_json: &str, now: &str) -> AppResult<()> {
    conn.execute(
        "INSERT INTO settings (key,value_json,updated_at) VALUES (?1,?2,?3)
         ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        params![key, value_json, now],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::infra::db::{now_iso, Db};

    #[test]
    fn upsert_overwrites() {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(&dir.path().join("t.db")).unwrap();
        db.migrate().unwrap();
        db.with(|c| {
            set_raw(c, "theme", "\"dark\"", &now_iso())?;
            set_raw(c, "theme", "\"light\"", &now_iso())?;
            assert_eq!(get_raw(c, "theme")?.as_deref(), Some("\"light\""));
            assert_eq!(get_raw(c, "missing")?, None);
            Ok(())
        })
        .unwrap();
    }
}
