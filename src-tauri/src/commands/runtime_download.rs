//! 从 GitHub Release 下载运行时并安装。
//!
//! 运行时机包体积约 3.5GB，被切成多个 <2GB 的分卷（GitHub 单附件上限 2GB）。
//! 下载、校验、合并、解压都在这里完成，前端只负责显示进度。

use std::path::{Path, PathBuf};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, State};

use crate::error::{AppError, AppResult};
use crate::AppPaths;

/// 运行时包与安装包同版本发布：每个 Release 都带自己的 runtime 分卷。
const RUNTIME_TAG: &str = concat!("v", env!("CARGO_PKG_VERSION"));

fn manifest_url() -> String {
    format!("https://github.com/wisdomWindy/svc-studio/releases/download/{RUNTIME_TAG}/runtime-manifest.json")
}

fn asset_base() -> String {
    format!("https://github.com/wisdomWindy/svc-studio/releases/download/{RUNTIME_TAG}")
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeInstallStatus {
    pub ready: bool,
    pub python_path: Option<String>,
    pub bytes: u64,
}

fn curl_path() -> PathBuf {
    // Windows 10 1803+ 自带 curl；用绝对路径避免被 PATH 里其他 curl 抢走
    let system = PathBuf::from(r"C:\Windows\System32\curl.exe");
    if system.is_file() {
        system
    } else {
        PathBuf::from("curl.exe")
    }
}

fn sha256_of(path: &Path) -> AppResult<String> {
    use std::io::Read as _;
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 4 * 1024 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// curl 不弹黑框；静默失败由调用方判断文件是否存在。
fn run_curl(args: &[String]) -> AppResult<std::process::ExitStatus> {
    let mut cmd = std::process::Command::new(curl_path());
    crate::core::proc::hide_console(&mut cmd);
    cmd.args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    Ok(cmd.status()?)
}

/// 单次 curl：自带重试、连接超时与低速断流保护。
fn curl_once(url: &str, dest: &Path, resume: bool) -> AppResult<bool> {
    let mut args: Vec<String> = vec![
        "-L".into(),
        "--fail".into(),
        "--retry".into(),
        "8".into(),
        "--retry-delay".into(),
        "3".into(),
        "--retry-all-errors".into(),
        "--connect-timeout".into(),
        "20".into(),
        "--speed-limit".into(),
        "10240".into(),
        "--speed-time".into(),
        "60".into(),
        "-o".into(),
        dest.to_string_lossy().to_string(),
    ];
    if resume {
        args.push("-C".into());
        args.push("-".into());
    }
    if let Some(proxy) = proxy_arg() {
        args.push("--proxy".into());
        args.push(proxy);
    }
    args.push("--http1.1".into());
    args.push(url.to_string());

    let status = run_curl(&args)?;
    let size = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
    Ok(status.success() && size > 0)
}

/// 分卷下载：断点续传 + 多轮重试 + sha256 校验；失败时保留已下载字节。
fn download_part(url: &str, dest: &Path, expect_sha: &str, label: &str) -> AppResult<u64> {
    const MAX_ATTEMPTS: u32 = 12;
    for attempt in 1..=MAX_ATTEMPTS {
        match curl_once(url, dest, dest.is_file()) {
            Ok(true) => {
                let size = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
                if expect_sha.is_empty() || sha256_of(dest)? == expect_sha {
                    return Ok(size);
                }
                let _ = std::fs::remove_file(dest);
                tracing::warn!(part = label, attempt, "sha mismatch, restarting part");
            }
            Ok(false) => tracing::warn!(part = label, attempt, "attempt failed"),
            Err(e) => tracing::warn!(part = label, attempt, error = %e, "attempt errored"),
        }
        std::thread::sleep(std::time::Duration::from_secs(
            2 * u64::from(attempt.min(5)),
        ));
    }
    Err(Box::new(AppError::new(
        "SVC-5001",
        format!("分卷下载失败：{label}"),
        "已重试 12 次；网络恢复后再点一次「自动下载运行时」会从断点继续",
    )))
}

/// curl 不读 Windows 的 WinINET 代理设置：先看环境变量，再探测常见本地代理端口。
fn proxy_arg() -> Option<String> {
    for key in ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"] {
        if let Ok(v) = std::env::var(key) {
            let v = v.trim().to_string();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    for port in [7897u16, 7890, 10809, 1080] {
        if std::net::TcpStream::connect_timeout(
            &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
            std::time::Duration::from_millis(150),
        )
        .is_ok()
        {
            return Some(format!("http://127.0.0.1:{port}"));
        }
    }
    None
}

/// 启动后台安装任务并立即返回。
///
/// 下载 3.6GB 远超 IPC 的 15 秒超时，所以命令本身只负责启动，
/// 进度与结果通过 `runtime-progress` 事件回传（stage: done/error）。
#[tauri::command]
pub fn install_runtime_from_release(app: AppHandle, state: State<'_, AppPaths>) -> AppResult<()> {
    let root = state.data_root.clone();
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        match install_inner(handle.clone(), root).await {
            Ok(status) => {
                handle
                    .emit(
                        "runtime-progress",
                        serde_json::json!({
                            "stage": "done",
                            "percent": 100,
                            "ready": status.ready,
                            "pythonPath": status.python_path,
                        }),
                    )
                    .ok();
            }
            Err(e) => {
                handle
                    .emit(
                        "runtime-progress",
                        serde_json::json!({
                            "stage": "error",
                            "percent": 0,
                            "code": e.code,
                            "message": e.message,
                            "cause": e.cause,
                        }),
                    )
                    .ok();
            }
        }
    });
    Ok(())
}

async fn install_inner(
    app: AppHandle,
    root: std::path::PathBuf,
) -> AppResult<RuntimeInstallStatus> {
    let downloads = root.join("downloads");
    std::fs::create_dir_all(&downloads)?;

    app.emit(
        "runtime-progress",
        serde_json::json!({ "stage": "manifest", "percent": 0 }),
    )
    .ok();

    // 1) 清单
    let manifest_path = downloads.join("runtime-manifest.json");
    let _ = std::fs::remove_file(&manifest_path);
    download_part(&manifest_url(), &manifest_path, "", "runtime-manifest.json")
        .map(|_| ())
        .map_err(|e| {
            Box::new(AppError::new(
                "SVC-5001",
                "无法获取运行时清单",
                format!("{e}；可手动从 Release 页面下载分卷"),
            ))
        })?;
    let raw = std::fs::read(&manifest_path)?;
    let head = String::from_utf8_lossy(&raw[..raw.len().min(160)]).to_string();
    let manifest: serde_json::Value = serde_json::from_slice(&raw).map_err(|e| {
        Box::new(AppError::new(
            "SVC-5001",
            "运行时清单格式错误",
            format!("{e}；返回内容开头：{}", head.trim()),
        ))
    })?;

    let parts = manifest
        .get("parts")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if parts.is_empty() {
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时清单为空",
            "manifest.parts 为空",
        )));
    }
    let total_mb: f64 = parts
        .iter()
        .filter_map(|p| p.get("mb").and_then(|v| v.as_f64()))
        .sum();

    // 2) 逐卷下载（已存在且校验通过则跳过）
    let mut downloaded: Vec<PathBuf> = Vec::new();
    let mut done_mb = 0f64;
    for part in &parts {
        let name = part
            .get("name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| Box::new(AppError::new("SVC-5001", "清单缺少 name", "")))?;
        let expect_sha = part
            .get("sha256")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let expect_mb = part.get("mb").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let dest = downloads.join(name);

        let mut ok = false;
        if dest.is_file() {
            if let Ok(sha) = sha256_of(&dest) {
                ok = !expect_sha.is_empty() && sha == expect_sha;
            }
        }
        if !ok {
            let url = format!("{}/{name}", asset_base());
            app.emit(
                "runtime-progress",
                serde_json::json!({
                    "stage": "download",
                    "file": name,
                    "percent": if total_mb > 0.0 { (done_mb / total_mb * 100.0).round() } else { 0.0 },
                }),
            )
            .ok();
            download_part(&url, &dest, &expect_sha, name)?;
        }
        done_mb += expect_mb;
        downloaded.push(dest);
    }

    app.emit(
        "runtime-progress",
        serde_json::json!({ "stage": "extract", "percent": 100 }),
    )
    .ok();

    // 3) 合并 + 校验 zip
    let zip_path = downloads.join("runtime.zip");
    {
        use std::io::Write as _;
        let mut out = std::fs::File::create(&zip_path)?;
        for part in &downloaded {
            let mut input = std::fs::File::open(part)?;
            std::io::copy(&mut input, &mut out)?;
        }
        out.flush()?;
    }
    let expect_zip = manifest
        .get("zipSha256")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    if !expect_zip.is_empty() {
        let actual = sha256_of(&zip_path)?;
        if actual != expect_zip {
            return Err(Box::new(AppError::new(
                "SVC-5001",
                "运行时包校验失败",
                format!("期望 {expect_zip}，实际 {actual}"),
            )));
        }
    }

    // 4) 先解压到临时目录，校验通过后再替换旧 runtime：
    //    这样解压中途失败不会留下半截运行时。
    let target = root.join("runtime");
    let staging = root.join("runtime.new");
    if staging.exists() {
        std::fs::remove_dir_all(&staging)?;
    }
    std::fs::create_dir_all(&staging)?;
    let mut tar = std::process::Command::new("tar");
    crate::core::proc::hide_console(&mut tar);
    let status = tar
        .arg("-xf")
        .arg(&zip_path)
        .arg("-C")
        .arg(&staging)
        .status()?;
    if !status.success() {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时解压失败",
            format!("tar 退出码 {:?}；已下载的分卷保留，可重试", status.code()),
        )));
    }
    if !staging.join("python.exe").is_file() {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时包不完整",
            "解压后没有 python.exe；已下载的分卷保留，可重试",
        )));
    }
    if target.exists() {
        std::fs::remove_dir_all(&target).map_err(|e| {
            Box::new(AppError::new(
                "SVC-5001",
                "旧运行时正在使用",
                format!("请先关闭任务并退出应用：{e}"),
            ))
        })?;
    }
    std::fs::rename(&staging, &target)?;
    let python = target.join("python.exe");
    app.emit(
        "runtime-progress",
        serde_json::json!({ "stage": "done", "percent": 100 }),
    )
    .ok();
    if !python.is_file() {
        return Err(Box::new(AppError::new(
            "SVC-5001",
            "运行时包不完整",
            "解压后没有 python.exe",
        )));
    }

    // 5) 成功后清掉下载产物（3.6GB 分卷 + 合并包），失败时保留以便断点续传
    let _ = std::fs::remove_file(&zip_path);
    for part in &downloaded {
        let _ = std::fs::remove_file(part);
    }
    let _ = std::fs::remove_file(&manifest_path);
    tracing::info!(
        freed = downloaded.len(),
        "runtime installed; download cache cleaned"
    );

    let bytes = std::fs::metadata(&python).map(|m| m.len()).unwrap_or(0);
    Ok(RuntimeInstallStatus {
        ready: true,
        python_path: Some(python.to_string_lossy().to_string()),
        bytes,
    })
}
