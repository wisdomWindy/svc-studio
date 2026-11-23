use rusqlite::{params, Connection, OptionalExtension};

use crate::core::models::ModelCard;
use crate::error::{AppError, AppResult};

pub struct NewModel<'a> {
    pub id: &'a str,
    pub project_id: Option<&'a str>,
    pub name: &'a str,
    pub step: Option<i64>,
    pub path: &'a str,
    pub sha256: &'a str,
    pub bytes: Option<i64>,
    pub speech_encoder: &'a str,
    pub f0_predictor: Option<&'a str>,
    pub vocoder: Option<&'a str>,
    pub vol_embedding: bool,
    pub config_path: &'a str,
    pub config_sha256: Option<&'a str>,
    pub dataset_fingerprint: Option<&'a str>,
    pub source: &'a str,
}

const COLUMNS: &str = "id,project_id,name,step,path,sha256,bytes,speech_encoder,f0_predictor,vocoder,vol_embedding,config_path,config_sha256,dataset_fingerprint,train_run_id,diffusion_path,cluster_path,index_path,source,status,mixed,compat_json,origin_dir,copied,required_assets_json,created_at";

fn map(row: &rusqlite::Row<'_>) -> rusqlite::Result<ModelCard> {
    Ok(ModelCard {
        id: row.get(0)?,
        project_id: row.get(1)?,
        name: row.get(2)?,
        step: row.get(3)?,
        path: row.get(4)?,
        sha256: row.get(5)?,
        bytes: row.get(6)?,
        speech_encoder: row.get(7)?,
        f0_predictor: row.get(8)?,
        vocoder: row.get(9)?,
        vol_embedding: row.get::<_, i64>(10)? == 1,
        config_path: row.get(11)?,
        config_sha256: row.get(12)?,
        dataset_fingerprint: row.get(13)?,
        train_run_id: row.get(14)?,
        diffusion_path: row.get(15)?,
        cluster_path: row.get(16)?,
        index_path: row.get(17)?,
        source: row.get(18)?,
        status: row.get(19)?,
        mixed: row.get::<_, i64>(20)? == 1,
        compat_json: row.get(21)?,
        origin_dir: row.get(22)?,
        copied: row.get::<_, i64>(23)? == 1,
        required_assets_json: row.get(24)?,
        created_at: row.get(25)?,
    })
}

pub fn insert(conn: &Connection, now: &str, m: &NewModel<'_>) -> AppResult<ModelCard> {
    // 同一权重文件（sha256 相同）只登记一次：重复导入应提示"已在库中"，而不是产生两张卡。
    if let Some(existing) = find_by_sha(conn, m.sha256)? {
        return Err(Box::new(AppError {
            code: "SVC-2002".to_string(),
            severity: crate::error::Severity::Warning,
            message: "同一模型已在库中".to_string(),
            cause: format!("sha256={} 已登记为 {}", m.sha256, existing.name),
            next_action: crate::error::NextAction {
                label: "查看已有模型".to_string(),
                deeplink: Some("/models".to_string()),
            },
            detail: Some(existing.id),
            retriable: false,
        }));
    }
    conn.execute(
        "INSERT INTO models
         (id,project_id,name,step,path,sha256,bytes,speech_encoder,f0_predictor,vocoder,vol_embedding,
          config_path,config_sha256,dataset_fingerprint,source,status,mixed,copied,created_at)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,'ready',0,?16,?17)",
        params![
            m.id,
            m.project_id,
            m.name,
            m.step,
            m.path,
            m.sha256,
            m.bytes,
            m.speech_encoder,
            m.f0_predictor,
            m.vocoder,
            i64::from(m.vol_embedding),
            m.config_path,
            m.config_sha256,
            m.dataset_fingerprint,
            m.source,
            i64::from(m.source == "imported"),
            now
        ],
    )
    .map_err(|e| {
        if format!("{e}").contains("UNIQUE") {
            Box::new(AppError::new(
                "SVC-2002",
                "同一模型已在库中",
                format!("sha256={}", m.sha256),
            ))
        } else {
            Box::new(AppError::new("SVC-7003", "模型登记失败", e.to_string()))
        }
    })?;
    get(conn, m.id)?.ok_or_else(|| Box::new(AppError::new("SVC-7003", "模型写入后读不回来", m.id)))
}

pub fn get(conn: &Connection, id: &str) -> AppResult<Option<ModelCard>> {
    let sql = format!("SELECT {COLUMNS} FROM models WHERE id = ?1");
    Ok(conn.query_row(&sql, [id], map).optional()?)
}

pub fn find_by_path(conn: &Connection, path: &str) -> AppResult<Option<ModelCard>> {
    let sql = format!("SELECT {COLUMNS} FROM models WHERE path = ?1 LIMIT 1");
    let mut stmt = conn.prepare(&sql)?;
    let row = stmt.query_row([path], map).optional()?;
    Ok(row)
}

pub fn find_by_sha(conn: &Connection, sha: &str) -> AppResult<Option<ModelCard>> {
    let sql = format!("SELECT {COLUMNS} FROM models WHERE sha256 = ?1");
    Ok(conn.query_row(&sql, [sha], map).optional()?)
}

pub fn list(conn: &Connection) -> AppResult<Vec<ModelCard>> {
    let sql =
        format!("SELECT {COLUMNS} FROM models WHERE status != 'missing' ORDER BY created_at DESC");
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map([], map)?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

pub fn set_status(conn: &Connection, id: &str, status: &str) -> AppResult<()> {
    let n = conn.execute(
        "UPDATE models SET status = ?2 WHERE id = ?1",
        params![id, status],
    )?;
    if n == 0 {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "模型不存在",
            id.to_string(),
        )));
    }
    Ok(())
}

/// 删除前必须由调用方做占用检查（默认模型 / 运行中推理）。
pub fn delete(conn: &Connection, id: &str) -> AppResult<()> {
    let n = conn.execute("DELETE FROM models WHERE id = ?1", [id])?;
    if n == 0 {
        return Err(Box::new(AppError::new(
            "SVC-7004",
            "模型不存在",
            id.to_string(),
        )));
    }
    Ok(())
}

/// 启动对账：磁盘上已不存在的模型标 missing（不删记录，保留历史与追溯）。
pub fn mark_missing(conn: &Connection) -> AppResult<Vec<String>> {
    let mut stmt =
        conn.prepare("SELECT id,path FROM models WHERE status IN ('ready','compressed')")?;
    let rows: Vec<(String, String)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    drop(stmt);

    let mut changed = Vec::new();
    for (id, path) in rows {
        if !std::path::Path::new(&path).exists() {
            conn.execute("UPDATE models SET status='missing' WHERE id=?1", [&id])?;
            changed.push(id);
        }
    }
    Ok(changed)
}

/// 模型文件换位置（统一迁移到 data/models 时用）
pub fn relink(conn: &Connection, id: &str, path: &str, config_path: &str) -> AppResult<()> {
    conn.execute(
        "UPDATE models SET path = ?1, config_path = ?2 WHERE id = ?3",
        rusqlite::params![path, config_path, id],
    )?;
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

    fn newm<'a>(id: &'a str, sha: &'a str) -> NewModel<'a> {
        NewModel {
            id,
            project_id: None,
            name: "小满@2400",
            step: Some(2400),
            path: "C:/models/g.pth",
            sha256: sha,
            bytes: Some(123),
            speech_encoder: "vec768l12",
            f0_predictor: Some("rmvpe"),
            vocoder: Some("nsf-hifigan"),
            vol_embedding: false,
            config_path: "C:/models/config.json",
            config_sha256: Some("cfg"),
            dataset_fingerprint: Some("ds"),
            source: "imported",
        }
    }

    #[test]
    fn insert_find_list_delete() {
        let (_d, db) = fixture();
        db.with(|c| {
            let m = insert(c, &now_iso(), &newm("mdl_1", "abc"))?;
            assert_eq!(m.status, "ready");
            assert!(m.copied);
            assert_eq!(find_by_sha(c, "abc")?.unwrap().id, "mdl_1");
            assert_eq!(list(c)?.len(), 1);
            delete(c, "mdl_1")?;
            assert!(get(c, "mdl_1")?.is_none());
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn duplicate_sha_reports_2002() {
        let (_d, db) = fixture();
        db.with(|c| {
            insert(c, &now_iso(), &newm("mdl_1", "same"))?;
            let err = insert(c, &now_iso(), &newm("mdl_2", "same")).unwrap_err();
            assert_eq!(err.code, "SVC-2002");
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn mark_missing_hides_deleted_files() {
        let (_d, db) = fixture();
        db.with(|c| {
            insert(c, &now_iso(), &newm("mdl_1", "s1"))?;
            let changed = mark_missing(c)?;
            assert_eq!(changed, vec!["mdl_1".to_string()]);
            assert_eq!(get(c, "mdl_1")?.unwrap().status, "missing");
            assert!(list(c)?.is_empty());
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn missing_models_are_hidden_from_library() {
        let (_d, db) = fixture();
        db.with(|c| {
            insert(c, &now_iso(), &newm("mdl_1", "s1"))?;
            insert(c, &now_iso(), &newm("mdl_2", "s2"))?;
            set_status(c, "mdl_2", "missing")?;
            assert_eq!(list(c)?.len(), 1);
            Ok(())
        })
        .unwrap();
    }
}
