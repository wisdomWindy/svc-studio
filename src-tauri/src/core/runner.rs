//! 运行中的作业注册表：保存 RunningJob 以便取消；进程退出时清理。

use std::collections::HashMap;
use std::sync::Mutex;

use crate::bridge::host::RunningJob;

#[derive(Default)]
pub struct Runner {
    running: Mutex<HashMap<String, RunningJob>>,
}

impl Runner {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&self, job: RunningJob) {
        if let Ok(mut map) = self.running.lock() {
            map.insert(job.job_id.clone(), job);
        }
    }

    pub fn take(&self, job_id: &str) -> Option<RunningJob> {
        self.running.lock().ok()?.remove(job_id)
    }

    pub fn contains(&self, job_id: &str) -> bool {
        self.running
            .lock()
            .map(|m| m.contains_key(job_id))
            .unwrap_or(false)
    }

    pub fn len(&self) -> usize {
        self.running.lock().map(|m| m.len()).unwrap_or(0)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_runner_reports_zero() {
        let runner = Runner::new();
        assert!(runner.is_empty());
        assert!(runner.take("nope").is_none());
        assert!(!runner.contains("nope"));
    }
}
