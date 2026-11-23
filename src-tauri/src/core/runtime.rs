//! 运行时与 worker 目录解析。
//!
//! 开发时 exe 在 `src-tauri/target/debug`；安装后 exe 在安装目录，资源在 `resources/` 下。
//! 两种布局都要能找到 `python.exe`、`worker/svc_bridge` 与上游源码，环境变量可覆盖（排障用）。

use std::path::{Path, PathBuf};

pub const PYTHON_ENV: &str = "SVCSTUDIO_PYTHON";
pub const WORKER_ENV: &str = "SVCSTUDIO_WORKER_DIR";
pub const UPSTREAM_ENV: &str = "SVCSTUDIO_UPSTREAM_DIR";

/// 安装版：`<install>/resources/runtime/python.exe`；开发版：`<target>/runtime/python.exe`。
const PYTHON_CANDIDATES: &[&str] = &[
    "resources/runtime/python.exe",
    "resources/python.exe",
    "runtime/python.exe",
    "../runtime/python.exe",
    "../../runtime/python.exe",
];

/// 运行时也可能放在数据根（用户解压 runtime 包后），与安装目录并列查找。
fn data_root_runtime() -> Option<PathBuf> {
    crate::error::data_root()
        .ok()
        .map(|root| root.join("runtime").join("python.exe"))
        .filter(|p| p.is_file())
}

const WORKER_CANDIDATES: &[&str] = &[
    "resources/runtime/worker",
    "resources/worker",
    "worker",
    "runtime/worker",
    "../worker",
    "../../worker",
    "../../../worker",
];

const UPSTREAM_CANDIDATES: &[&str] = &[
    "resources/upstream",
    "upstream",
    "../upstream",
    "../vendor/so-vits-svc",
    "../../vendor/so-vits-svc",
    "../../../vendor/so-vits-svc",
];

fn env_override(key: &str) -> Option<PathBuf> {
    std::env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

pub fn pick_python(dir: &Path) -> Option<PathBuf> {
    PYTHON_CANDIDATES
        .iter()
        .map(|rel| dir.join(rel))
        .find(|p| p.is_file())
}

pub fn pick_worker(dir: &Path) -> Option<PathBuf> {
    WORKER_CANDIDATES
        .iter()
        .map(|rel| dir.join(rel))
        .find(|p| p.join("svc_bridge").is_dir())
}

pub fn pick_upstream(dir: &Path) -> Option<PathBuf> {
    UPSTREAM_CANDIDATES
        .iter()
        .map(|rel| dir.join(rel))
        .find(|p| p.join("resample.py").is_file())
}

/// 内置 ffmpeg：安装目录 `resources/bin/ffmpeg.exe`，或数据目录 `bin/ffmpeg.exe`。
/// 找不到时返回空串，worker 会回落到 PATH。
pub fn resolve_ffmpeg() -> PathBuf {
    if let Ok(raw) = std::env::var("SVCSTUDIO_FFMPEG") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            let path = PathBuf::from(trimmed);
            if path.is_file() {
                return path;
            }
        }
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("resources").join("bin").join("ffmpeg.exe"));
        candidates.push(dir.join("resources").join("ffmpeg.exe"));
    }
    if let Ok(root) = crate::error::data_root() {
        candidates.push(root.join("bin").join("ffmpeg.exe"));
        candidates.push(root.join("ffmpeg.exe"));
    }
    candidates
        .into_iter()
        .find(|p| p.is_file())
        .unwrap_or_default()
}

pub fn worker_env(worker_dir: &Path) -> Vec<(String, String)> {
    let upstream = resolve_upstream_dir();
    // 只允许 worker 与上游进入 PYTHONPATH：不继承宿主环境，避免把系统 Python 的
    // site-packages 泄漏进子进程（软件必须与全局 Python 完全隔离）。
    let path_parts = vec![worker_dir.to_path_buf(), upstream];
    let joined = std::env::join_paths(path_parts).unwrap_or_default();
    vec![
        (
            "PYTHONPATH".to_string(),
            joined.to_string_lossy().to_string(),
        ),
        ("PYTHONIOENCODING".to_string(), "utf-8".to_string()),
        ("PYTHONUNBUFFERED".to_string(), "1".to_string()),
        ("PYTHONNOUSERSITE".to_string(), "1".to_string()),
        ("PYTHONSAFEPATH".to_string(), "1".to_string()),
        (
            "SVCSTUDIO_SEPARATOR_DIR".to_string(),
            resolve_separator_dir().to_string_lossy().to_string(),
        ),
        (
            "SVCSTUDIO_UPSTREAM_DIR".to_string(),
            resolve_upstream_dir().to_string_lossy().to_string(),
        ),
        ("SVCSTUDIO_RUNTIME_KIND".to_string(), "gpu".to_string()),
        (
            "SVCSTUDIO_FFMPEG".to_string(),
            resolve_ffmpeg().to_string_lossy().to_string(),
        ),
    ]
}

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf))
}

/// 分离模型目录：<安装目录>/resources/separator
pub fn resolve_separator_dir() -> PathBuf {
    if let Some(dir) = exe_dir() {
        let p = dir.join("resources").join("separator");
        if p.is_dir() {
            return p;
        }
    }
    PathBuf::from("resources").join("separator")
}

pub fn resolve_python() -> PathBuf {
    if let Some(p) = env_override(PYTHON_ENV) {
        return p;
    }
    if let Some(dir) = exe_dir() {
        if let Some(found) = pick_python(&dir) {
            return found;
        }
    }
    if let Some(found) = data_root_runtime() {
        return found;
    }
    PathBuf::from("python")
}

pub fn resolve_worker_dir() -> PathBuf {
    if let Some(p) = env_override(WORKER_ENV) {
        return p;
    }
    if let Some(dir) = exe_dir() {
        if let Some(found) = pick_worker(&dir) {
            return found;
        }
    }
    if let Ok(root) = crate::error::data_root() {
        for rel in ["runtime/worker", "worker"] {
            let candidate = root.join(rel);
            if candidate.join("svc_bridge").is_dir() {
                return candidate;
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(found) = pick_worker(&cwd) {
            return found;
        }
    }
    PathBuf::from("worker")
}

pub fn resolve_upstream_dir() -> PathBuf {
    if let Some(p) = env_override(UPSTREAM_ENV) {
        return p;
    }
    if let Some(dir) = exe_dir() {
        if let Some(found) = pick_upstream(&dir) {
            return found;
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(found) = pick_upstream(&cwd) {
            return found;
        }
    }
    PathBuf::from("vendor/so-vits-svc")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join(format!("svc-rt-{}", uuid::Uuid::now_v7().simple()))
            .join(format!("svcrt-{name}-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn installed_layout_wins_over_dev_layout() {
        let root = temp("installed");
        std::fs::create_dir_all(root.join("resources/runtime/worker/svc_bridge")).unwrap();
        std::fs::write(root.join("resources/runtime/python.exe"), b"x").unwrap();
        std::fs::create_dir_all(root.join("runtime")).unwrap();

        assert_eq!(
            pick_python(&root).unwrap(),
            root.join("resources/runtime/python.exe"),
            "安装版布局优先"
        );
        assert_eq!(
            pick_worker(&root).unwrap(),
            root.join("resources/runtime/worker")
        );
    }

    #[test]
    fn dev_layout_still_works() {
        let root = temp("dev");
        std::fs::create_dir_all(root.join("runtime/worker/svc_bridge")).unwrap();
        std::fs::write(root.join("runtime/python.exe"), b"x").unwrap();

        assert_eq!(pick_python(&root).unwrap(), root.join("runtime/python.exe"));
        assert_eq!(pick_worker(&root).unwrap(), root.join("runtime/worker"));
    }

    #[test]
    fn upstream_prefers_resources_copy() {
        let root = temp("upstream");
        std::fs::create_dir_all(root.join("resources/upstream")).unwrap();
        std::fs::write(root.join("resources/upstream/resample.py"), b"x").unwrap();
        std::fs::create_dir_all(root.join("vendor/so-vits-svc")).unwrap();
        std::fs::write(root.join("vendor/so-vits-svc/resample.py"), b"x").unwrap();

        assert_eq!(
            pick_upstream(&root).unwrap(),
            root.join("resources/upstream")
        );
    }

    #[test]
    fn nothing_found_returns_none() {
        let root = temp("empty");
        assert!(pick_python(&root).is_none());
        assert!(pick_worker(&root).is_none());
        assert!(pick_upstream(&root).is_none());
    }
}
#[cfg(test)]
mod env_tests {
    use super::*;

    #[test]
    fn worker_env_does_not_inherit_host_pythonpath() {
        // 不能改进程环境变量：测试是多线程的，新版 Rust 下 set_var 会 panic
        let env = worker_env(std::path::Path::new(r"C:\app\worker"));

        let value = env
            .iter()
            .find(|(k, _)| k == "PYTHONPATH")
            .map(|(_, v)| v.clone())
            .unwrap_or_default();
        assert!(value.contains("worker"), "必须包含内置 worker 目录");
        assert!(
            !value.contains("global\\site-packages"),
            "不得继承宿主 PYTHONPATH：{value}"
        );
        assert!(env.iter().any(|(k, v)| k == "PYTHONNOUSERSITE" && v == "1"));
    }
}
