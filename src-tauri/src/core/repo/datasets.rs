use rusqlite::{params, Connection, OptionalExtension};

use crate::core::models::DatasetSummary;
use crate::error::{AppError, AppResult};

const COLUMNS: &str = "id,project_id,version,root_path,file_count,total_seconds,fingerprint,low_data,qc_path,created_at";

fn map(row: &rusqlite::Row<'_>) -> rusqlite::Result<DatasetSummary> {
    Ok(DatasetSummary {
        id: row.get(0)?,
        project_id: row.get(1)?,
        version: row.get(2)?,
        root_path: row.get(3)?,
        file_count: row.get(4)?,
        total_seconds: row.get(5)?,
        fingerprint: row.get(6)?,
        low_data: row.get::<_, i64>(7)? == 1,
        qc_path: row.get(8)?,
        created_at: row.get(9)?,
    })
}

pub fn next_version(conn: &Connection, project_id: &str) -> AppResult<i64> {
    let v: i64 = conn.query_row(
        "SELECT COALESCE(MAX(version),0)+1 FROM datasets WHERE project_id = ?1",
        [project_id],
        |r| r.get(0),
    )?;
    Ok(v)
}

pub fn insert(
    conn: &Connection,
    id: &str,
    project_id: &str,
    root_path: &str,
    now: &str,
) -> AppResult<DatasetSummary> {
    let version = next_version(conn, project_id)?;
    conn.execute(
        "INSERT INTO datasets (id,project_id,version,root_path,created_at) VALUES (?1,?2,?3,?4,?5)",
        params![id, project_id, version, root_path, now],
    )?;
    get(conn, id)?.ok_or_else(|| Box::new(AppError::new("SVC-7003", "数据集写入后读不回来", id)))
}

pub fn get(conn: &Connection, id: &str) -> AppResult<Option<DatasetSummary>> {
    let sql = format!("SELECT {COLUMNS} FROM datasets WHERE id = ?1");
    Ok(conn.query_row(&sql, [id], map).optional()?)
}

pub fn list_by_project(conn: &Connection, project_id: &str) -> AppResult<Vec<DatasetSummary>> {
    let sql = format!("SELECT {COLUMNS} FROM datasets WHERE project_id = ?1 ORDER BY version DESC");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([project_id], map)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

pub fn finalize(
    conn: &Connection,
    id: &str,
    file_count: i64,
    total_seconds: f64,
    fingerprint: &str,
    low_data: bool,
) -> AppResult<()> {
    conn.execute(
        "UPDATE datasets SET file_count=?2, total_seconds=?3, fingerprint=?4, low_data=?5 WHERE id=?1",
        params![id, file_count, total_seconds, fingerprint, i64::from(low_data)],
    )?;
    Ok(())
}

pub struct NewDatasetFile<'a> {
    pub id: &'a str,
    pub dataset_id: &'a str,
    pub rel_path: &'a str,
    pub seconds: f64,
    pub sample_rate: i64,
    pub channels: i64,
    pub sha256: Option<&'a str>,
}

pub fn insert_file(conn: &Connection, f: &NewDatasetFile<'_>) -> AppResult<()> {
    conn.execute(
        "INSERT INTO dataset_files (id,dataset_id,rel_path,seconds,sample_rate,channels,sha256,status)
         VALUES (?1,?2,?3,?4,?5,?6,?7,'ok')",
        params![
            f.id,
            f.dataset_id,
            f.rel_path,
            f.seconds,
            f.sample_rate,
            f.channels,
            f.sha256
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{ids, repo::projects};
    use crate::infra::db::{now_iso, Db};

    fn fixture() -> (tempfile::TempDir, Db) {
        let dir = tempfile::tempdir().unwrap();
        let mut db = Db::open(&dir.path().join("t.db")).unwrap();
        db.migrate().unwrap();
        (dir, db)
    }

    #[test]
    fn versions_increment_per_project() {
        let (_d, db) = fixture();
        db.with(|c| {
            let now = now_iso();
            projects::create(
                c,
                "prj_1",
                &now,
                &projects::NewProject {
                    name: "p",
                    slug: "p",
                    root_path: "C:/p",
                    speaker: None,
                    speech_encoder: "vec768l12",
                    f0_predictor: "rmvpe",
                    material_type: "singing",
                    gpu_index: None,
                },
            )?;
            let d1 = insert(c, "ds_1", "prj_1", "C:/p/dataset_raw", &now)?;
            let d2 = insert(c, "ds_2", "prj_1", "C:/p/dataset_raw", &now)?;
            assert_eq!(d1.version, 1);
            assert_eq!(d2.version, 2);
            assert_eq!(list_by_project(c, "prj_1")?.len(), 2);
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn finalize_updates_aggregate() {
        let (_d, db) = fixture();
        db.with(|c| {
            let now = now_iso();
            projects::create(
                c,
                "prj_1",
                &now,
                &projects::NewProject {
                    name: "p",
                    slug: "p",
                    root_path: "C:/p",
                    speaker: None,
                    speech_encoder: "vec768l12",
                    f0_predictor: "rmvpe",
                    material_type: "singing",
                    gpu_index: None,
                },
            )?;
            insert(c, "ds_1", "prj_1", "C:/p", &now)?;
            insert_file(
                c,
                &NewDatasetFile {
                    id: "f1",
                    dataset_id: "ds_1",
                    rel_path: "spk/001.wav",
                    seconds: 8.5,
                    sample_rate: 44100,
                    channels: 1,
                    sha256: Some("sha"),
                },
            )?;
            finalize(c, "ds_1", 1, 8.5, ids::dataset_id().as_str(), true)?;
            let d = get(c, "ds_1")?.unwrap();
            assert_eq!(d.file_count, 1);
            assert!(d.low_data);
            Ok(())
        })
        .unwrap();
    }
}
