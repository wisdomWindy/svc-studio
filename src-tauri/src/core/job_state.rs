//! 任务状态机（spec §6）。迁移表是唯一事实，UI 与调度器都不得自行判断。

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobStatus {
    Blocked,
    Queued,
    Running,
    Stopping,
    Stopped,
    Succeeded,
    Failed,
    Cancelled,
    Interrupted,
}

impl JobStatus {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "blocked" => Self::Blocked,
            "queued" => Self::Queued,
            "running" => Self::Running,
            "stopping" => Self::Stopping,
            "stopped" => Self::Stopped,
            "succeeded" => Self::Succeeded,
            "failed" => Self::Failed,
            "cancelled" => Self::Cancelled,
            "interrupted" => Self::Interrupted,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Blocked => "blocked",
            Self::Queued => "queued",
            Self::Running => "running",
            Self::Stopping => "stopping",
            Self::Stopped => "stopped",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::Interrupted => "interrupted",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Succeeded | Self::Failed | Self::Cancelled | Self::Stopped | Self::Interrupted
        )
    }

    /// 是否允许"再跑一次"（重试/续训）。
    pub fn is_retriable(self) -> bool {
        matches!(
            self,
            Self::Failed | Self::Interrupted | Self::Cancelled | Self::Stopped
        )
    }

    pub fn can_transition_to(self, next: Self) -> bool {
        use JobStatus::*;
        matches!(
            (self, next),
            (Blocked, Queued)
                | (Queued, Running | Cancelled | Blocked)
                | (
                    Running,
                    Succeeded | Failed | Stopping | Cancelled | Interrupted
                )
                | (Stopping, Stopped | Failed | Interrupted | Cancelled)
                | (Failed | Interrupted | Cancelled | Stopped, Queued)
        )
    }
}

#[cfg(test)]
mod tests {
    use super::JobStatus::*;

    #[test]
    fn happy_path_is_allowed() {
        assert!(Queued.can_transition_to(Running));
        assert!(Running.can_transition_to(Succeeded));
        assert!(Running.can_transition_to(Stopping));
        assert!(Stopping.can_transition_to(Stopped));
        assert!(Stopped.can_transition_to(Queued));
    }

    #[test]
    fn terminal_states_do_not_go_backwards() {
        assert!(!Succeeded.can_transition_to(Running));
        assert!(!Succeeded.can_transition_to(Queued));
        assert!(!Cancelled.can_transition_to(Running));
    }

    #[test]
    fn running_cannot_jump_to_queued_without_stop() {
        assert!(!Running.can_transition_to(Queued));
    }

    #[test]
    fn retriable_set_matches_spec() {
        assert!(Failed.is_retriable());
        assert!(Interrupted.is_retriable());
        assert!(!Succeeded.is_retriable());
        assert!(!Running.is_retriable());
    }
}
