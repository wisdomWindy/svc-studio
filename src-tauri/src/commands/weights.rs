//! 预训练权重下载：编码器（ContentVec）、F0（RMVPE）、声码器（NSF-HiFiGAN）。
//!
//! 上游 so-vits-svc 只提供下载来源，不内置权重；这里让用户点一次按钮就把缺的补齐。

use std::path::PathBuf;

use tauri::{AppHandle, Emitter, State};

use crate::error::AppResult;
use crate::AppPaths;

const MIRROR: &str = "https://hf-mirror.com";

fn items(pretrain: &std::path::Path) -> Vec<(&'static str, String, PathBuf)> {
    vec![
        (
            "encoder（ContentVec vec768l12）",
            format!("{MIRROR}/lj1995/VoiceConversionWebUI/resolve/main/hubert_base.pt"),
            pretrain.join("checkpoint_best_legacy_500.pt"),
        ),
        (
            "f0（rmvpe）",
            format!("{MIRROR}/datasets/ylzz1997/rmvpe_pretrain_model/resolve/main/rmvpe.pt"),
            pretrain.join("rmvpe.pt"),
        ),
        (
            "vocoder（nsf_hifigan model）",
            format!("{MIRROR}/choihkk/nsf-hifigan/resolve/main/model"),
            pretrain.join("nsf_hifigan").join("model"),
        ),
        (
            "vocoder（nsf_hifigan config）",
            format!("{MIRROR}/choihkk/nsf-hifigan/resolve/main/config.json"),
            pretrain.join("nsf_hifigan").join("config.json"),
        ),
    ]
}

fn curl() -> PathBuf {
    let system = PathBuf::from(r"C:\Windows\System32\curl.exe");
    if system.is_file() {
        system
    } else {
        PathBuf::from("curl.exe")
    }
}

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

fn is_present(path: &std::path::Path) -> bool {
    std::fs::metadata(path)
        .map(|m| m.len() > 1024)
        .unwrap_or(false)
}

/// 后台下载缺失权重；进度通过 weights-progress 事件回传。
#[tauri::command]
pub fn download_missing_weights(app: AppHandle, state: State<'_, AppPaths>) -> AppResult<()> {
    let pretrain = state.data_root.join("pretrain");
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = std::fs::create_dir_all(&pretrain) {
            emit_error(&handle, &e.to_string());
            return;
        }
        let list = items(&pretrain);
        let total = list.len();
        for (idx, (label, url, out)) in list.iter().enumerate() {
            if is_present(out) {
                emit(&handle, label, idx + 1, total, "cached");
                continue;
            }
            emit(&handle, label, idx + 1, total, "downloading");
            if let Some(parent) = out.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let tmp = out.with_extension("part");
            let mut cmd = std::process::Command::new(curl());
            crate::core::proc::hide_console(&mut cmd);
            let mut args: Vec<String> = vec![
                "-L".into(),
                "--fail".into(),
                "--retry".into(),
                "3".into(),
                "--retry-delay".into(),
                "2".into(),
                "--http1.1".into(),
                "-o".into(),
                tmp.to_string_lossy().to_string(),
            ];
            if let Some(p) = proxy_arg() {
                args.push("--proxy".into());
                args.push(p);
            }
            args.push(url.clone());
            let status = cmd
                .args(&args)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status();
            match status {
                Ok(s) if s.success() && is_present(&tmp) => {
                    if std::fs::rename(&tmp, out).is_err() {
                        let _ = std::fs::remove_file(&tmp);
                        emit_error(&handle, &format!("{label} 写入失败"));
                        return;
                    }
                    emit(&handle, label, idx + 1, total, "done");
                }
                _ => {
                    let _ = std::fs::remove_file(&tmp);
                    emit_error(
                        &handle,
                        &format!("{label} 下载失败；可设置 HTTPS_PROXY 或手动放入 pretrain 目录"),
                    );
                    return;
                }
            }
        }
        handle
            .emit("weights-progress", serde_json::json!({ "stage": "done" }))
            .ok();
    });
    Ok(())
}

fn emit(app: &AppHandle, label: &str, index: usize, total: usize, stage: &str) {
    app.emit(
        "weights-progress",
        serde_json::json!({ "stage": stage, "label": label, "index": index, "total": total }),
    )
    .ok();
}

fn emit_error(app: &AppHandle, message: &str) {
    app.emit(
        "weights-progress",
        serde_json::json!({ "stage": "error", "message": message }),
    )
    .ok();
}

/// 供 UI 判断是否需要显示下载按钮。
#[tauri::command]
pub fn missing_weights(state: State<'_, AppPaths>) -> AppResult<Vec<String>> {
    let pretrain = state.data_root.join("pretrain");
    let missing = items(&pretrain)
        .into_iter()
        .filter(|(_, _, out)| !is_present(out))
        .map(|(label, _, _)| label.to_string())
        .collect();
    Ok(missing)
}
