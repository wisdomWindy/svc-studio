//! 领域层：ID、状态机、模型结构、仓储。
//!
//! WP1 先把完整领域 API 建齐（含 WP2+ 才会接入的写入路径），因此这里允许暂未接线的
//! 函数存在。**接入完成后必须删除下面的 allow**（task-board：W7-5 之后复查）。
pub mod assets;
pub mod diagnostics;
pub mod gate;
pub mod ids;
pub mod job_state;
pub mod model;
pub mod models;
pub mod proc;
pub mod projects_root;
pub mod repo;
pub mod runner;
pub mod runtime;
pub mod scheduler;
pub mod train;
pub mod worker_cli;
