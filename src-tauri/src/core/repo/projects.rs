use rusqlite::{params, Connection, OptionalExtension};

use crate::core::models::Project;
use crate::error::{AppError, AppResult};

pub struct NewProject<'a> {
    pub name: &'a str,
    pub slug: &'a str,
    pub root_path: &'a str,
    pub speaker: Option<&'a str>,
    pub speech_encoder: &'a str,
    pub f0_predictor: &'a str,
    pub material_type: &'a str,
    pub gpu_index: Option<i64>,
}

const COLUMNS: &str = "id,name,slug,root_path,speaker,speech_encoder,f0_predictor,material_type,gpu_index,status,created_at,updated_at";

fn map(row: &rusqlite::Row<'_>) -> rusqlite::Result<Project> {
    Ok(Project {
        id: row.get(0)?,
        name: row.get(1)?,
        slug: row.get(2)?,
        root_path: row.get(3)?,
        speaker: row.get(4)?,
        speech_encoder: row.get(5)?,
        f0_predictor: row.get(6)?,
        material_type: row.get(7)?,
        gpu_index: row.get(8)?,
        status: row.get(9)?,
        created_at: row.get(10)?,
        updated_at: row.get(11)?,
    })
}

pub fn create(conn: &Connection, id: &str, now: &str, p: &NewProject<'_>) -> AppResult<Project> {
    conn.execute(
        "INSERT INTO projects (id,name,slug,root_path,speaker,speech_encoder,f0_predictor,material_type,gpu_index,status,created_at,updated_at)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,'active',?10,?10)",
        params![
            id,
            p.name,
            p.slug,
            p.root_path,
            p.speaker,
            p.speech_encoder,
            p.f0_predictor,
            p.material_type,
            p.gpu_index,
            now
        ],
    )
    .map_err(|e| {
        Box::new(AppError::new(
            "SVC-3002",
            "项目创建失败",
            format!("{}: {e}", p.name),
        ))
    })?;
    get(conn, id)?.ok_or_else(|| Box::new(AppError::new("SVC-7003", "项目写入后读不回来", id)))
}

pub fn get(conn: &Connection, id: &str) -> AppResult<Option<Project>> {
    let sql = format!("SELECT {COLUMNS} FROM projects WHERE id = ?1");
    Ok(conn.query_row(&sql, [id], map).optional()?)
}

pub fn list(conn: &Connection) -> AppResult<Vec<Project>> {
    let sql = format!("SELECT {COLUMNS} FROM projects ORDER BY created_at DESC");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([], map)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

pub fn delete(conn: &Connection, id: &str) -> AppResult<()> {
    let n = conn.execute("DELETE FROM projects WHERE id = ?1", [id])?;
    if n == 0 {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "项目不存在",
            id.to_string(),
        )));
    }
    Ok(())
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

    fn newp<'a>() -> NewProject<'a> {
        NewProject {
            name: "小满",
            slug: "xiaoman",
            root_path: "C:/data/projects/xiaoman",
            speaker: Some("xiaoman"),
            speech_encoder: "vec768l12",
            f0_predictor: "rmvpe",
            material_type: "singing",
            gpu_index: Some(0),
        }
    }

    #[test]
    fn create_get_list_delete() {
        let (_d, db) = fixture();
        db.with(|c| {
            let p = create(c, "prj_1", &now_iso(), &newp())?;
            assert_eq!(p.status, "active");
            assert_eq!(p.material_type, "singing");
            assert_eq!(list(c)?.len(), 1);
            assert_eq!(get(c, "prj_1")?.unwrap().slug, "xiaoman");
            delete(c, "prj_1")?;
            assert!(get(c, "prj_1")?.is_none());
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn duplicate_slug_is_rejected_with_readable_error() {
        let (_d, db) = fixture();
        db.with(|c| {
            create(c, "prj_1", &now_iso(), &newp())?;
            let err = create(c, "prj_2", &now_iso(), &newp()).unwrap_err();
            assert_eq!(err.code, "SVC-3002");
            assert!(err.cause.contains("prj_2") || err.cause.contains("UNIQUE"));
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn delete_missing_project_reports_7004() {
        let (_d, db) = fixture();
        db.with(|c| {
            let err = delete(c, "nope").unwrap_err();
            assert_eq!(err.code, "SVC-7004");
            Ok(())
        })
        .unwrap();
    }
}
