//! 资源调度：GPU 单并发（spec §8）。同一时刻只允许一个吃显存的作业。
//!
//! 语义：`acquire()` 排队等待，permit drop 即释放；`in_use()` 供 UI 显示"GPU 忙"。
//! 优先级排序随任务队列一起落地（WP6 收尾），这里不提前造空壳。

use std::sync::Arc;

use tokio::sync::{OwnedSemaphorePermit, Semaphore};

#[derive(Clone)]
pub struct Scheduler {
    gpu: Arc<Semaphore>,
}

impl Scheduler {
    pub fn new() -> Self {
        Self {
            gpu: Arc::new(Semaphore::new(1)),
        }
    }

    pub async fn acquire(&self) -> OwnedSemaphorePermit {
        self.gpu
            .clone()
            .acquire_owned()
            .await
            .expect("semaphore is never closed")
    }

    pub fn in_use(&self) -> bool {
        self.gpu.available_permits() == 0
    }
}

impl Default for Scheduler {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[tokio::test]
    async fn only_one_holder_at_a_time() {
        let sched = Scheduler::new();
        assert!(!sched.in_use());
        let first = sched.acquire().await;
        assert!(sched.in_use(), "第一个持有者占用 GPU");

        let waiter = {
            let sched = sched.clone();
            tokio::spawn(async move {
                let _permit = sched.acquire().await;
                true
            })
        };
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(sched.in_use(), "等待者不应提前拿到锁");
        drop(first);
        let got = tokio::time::timeout(Duration::from_secs(2), waiter)
            .await
            .expect("waiter should proceed")
            .expect("join");
        assert!(got);
        assert!(!sched.in_use(), "释放后应重新空闲");
    }
}
