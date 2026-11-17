//! 子进程窗口控制。
//!
//! Windows 上控制台子进程（python.exe / cmd.exe / tar.exe / nvidia-smi）默认会创建
//! 一个控制台窗口，用户看到的就是一闪而过的黑框。所有 `Command` 都必须经过这里。

use std::process::Command;

#[cfg(windows)]
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// 让 `std::process::Command` 不弹控制台窗口。
pub fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = cmd;
    }
}

/// 让 `tokio::process::Command` 不弹控制台窗口（tokio 自带该固有方法）。
pub fn hide_console_async(cmd: &mut tokio::process::Command) {
    #[cfg(windows)]
    {
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = cmd;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hide_console_is_callable_for_std_command() {
        let mut cmd = Command::new("cmd");
        hide_console(&mut cmd);
    }

    #[test]
    fn hide_console_is_callable_for_tokio_command() {
        let mut cmd = tokio::process::Command::new("cmd");
        hide_console_async(&mut cmd);
    }
}
