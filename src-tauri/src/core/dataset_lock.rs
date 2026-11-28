//! 数据集操作的互斥锁：同一个项目同一时刻只允许一个会写数据的操作。
//!
//! 为什么需要：分离/切片可能跑几分钟，用户切走页面再回来，界面上的 busy 状态已经重置；
//! 此时若自动跑质检，QC 会读到写到一半的数据；两个操作同时写同一个目录也会互相破坏。
//! 因此锁必须放在**主进程**（与界面生命周期无关），而不是前端状态里。
//!
//! `acquire` 以 `&Arc<Self>` 调用，返回的 guard 是 `'static`，可以直接移进
//! `tauri::async_runtime::spawn` 的任务里；drop 时自动释放（成功/失败/取消都不泄漏）。

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::error::{AppError, AppResult};

#[derive(Debug, Clone)]
pub struct BusyInfo {
    pub op: &'static str,
    pub started_ms: u64,
}

/// 每个项目一把锁。
#[derive(Default, Debug)]
pub struct DatasetLock {
    inner: Mutex<HashMap<String, BusyInfo>>,
}

/// 进程启动时刻，用来算"当前操作已经跑了多久"。
fn process_elapsed_ms() -> u64 {
    static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    START
        .get_or_init(std::time::Instant::now)
        .elapsed()
        .as_millis() as u64
}

impl DatasetLock {
    pub fn new() -> Self {
        Self::default()
    }

    /// 拿锁；已被占用则返回 SVC-3010，并说明当前在跑什么。
    pub fn acquire(
        self: &Arc<Self>,
        project_id: &str,
        op: &'static str,
    ) -> AppResult<DatasetGuard> {
        let mut map = self.inner.lock().map_err(|_| {
            Box::new(AppError::new(
                "SVC-3010",
                "数据集锁不可用",
                "mutex poisoned",
            ))
        })?;
        if let Some(current) = map.get(project_id) {
            return Err(Box::new(AppError::new(
                "SVC-3010",
                "数据集正在执行其他操作",
                format!("当前：{}；请等它结束再执行「{op}」", current.op),
            )));
        }
        map.insert(
            project_id.to_string(),
            BusyInfo {
                op,
                started_ms: process_elapsed_ms(),
            },
        );
        Ok(DatasetGuard {
            lock: Arc::clone(self),
            project_id: project_id.to_string(),
        })
    }

    /// 当前状态：None 表示空闲（界面据此禁用按钮 / 跳过自动质检）。
    pub fn current(&self, project_id: &str) -> Option<BusyInfo> {
        self.inner
            .lock()
            .ok()
            .and_then(|map| map.get(project_id).cloned())
    }

    fn release(&self, project_id: &str) {
        if let Ok(mut map) = self.inner.lock() {
            map.remove(project_id);
        }
    }
}

#[derive(Debug)]
pub struct DatasetGuard {
    lock: Arc<DatasetLock>,
    project_id: String,
}

impl Drop for DatasetGuard {
    fn drop(&mut self) {
        self.lock.release(&self.project_id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lock() -> Arc<DatasetLock> {
        Arc::new(DatasetLock::new())
    }

    #[test]
    fn second_operation_is_rejected_until_first_finishes() {
        let lock = lock();
        assert!(lock.current("prj").is_none());

        let guard = lock.acquire("prj", "separate").expect("第一次应成功");
        let err = lock.acquire("prj", "qc").expect_err("第二次必须被拒绝");
        assert_eq!(err.code, "SVC-3010");
        assert!(err.cause.contains("separate"));
        assert_eq!(lock.current("prj").unwrap().op, "separate");

        drop(guard);
        assert!(lock.current("prj").is_none(), "释放后应变空闲");
        assert!(lock.acquire("prj", "qc").is_ok());
    }

    #[test]
    fn different_projects_do_not_block_each_other() {
        let lock = lock();
        let _a = lock.acquire("prj_a", "separate").unwrap();
        assert!(lock.acquire("prj_b", "qc").is_ok());
    }

    #[test]
    fn guard_releases_even_when_operation_fails() {
        let lock = lock();
        {
            let _g = lock.acquire("prj", "slice").unwrap();
            // 模拟操作失败提前返回：guard 仍会 drop
        }
        assert!(lock.current("prj").is_none());
    }

    #[test]
    fn guard_can_move_into_spawned_task() {
        // dataset_prepare 会把 guard 移进 async 任务里；这里用线程模拟
        let lock = lock();
        let guard = lock.acquire("prj", "prepare").unwrap();
        let handle = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(20));
            drop(guard);
        });
        handle.join().unwrap();
        assert!(lock.current("prj").is_none());
    }
}
