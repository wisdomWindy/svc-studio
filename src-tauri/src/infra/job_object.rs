//! Windows Job Object：把 worker 及其所有子进程绑在一个作业里，取消时整棵树一起终止。
//! 非 Windows 平台退化为按 pid 终止。

#[cfg(windows)]
mod imp {
    use std::io;
    use std::os::windows::io::RawHandle;

    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct JobGuard(HANDLE);

    // HANDLE 在内核对象语义上跨线程安全；JobGuard 只持有句柄。
    unsafe impl Send for JobGuard {}
    unsafe impl Sync for JobGuard {}

    impl JobGuard {
        pub fn new() -> io::Result<Self> {
            unsafe {
                let handle = CreateJobObjectW(None, None)
                    .map_err(|e| io::Error::other(format!("CreateJobObjectW failed: {e}")))?;
                let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
                .map_err(|e| io::Error::other(format!("SetInformationJobObject failed: {e}")))?;
                Ok(Self(handle))
            }
        }

        pub fn assign(&self, process: RawHandle) -> io::Result<()> {
            unsafe {
                AssignProcessToJobObject(self.0, HANDLE(process as _))
                    .map_err(|e| io::Error::other(format!("AssignProcessToJobObject failed: {e}")))
            }
        }

        /// 终止作业内所有进程（含孙进程），不等待。
        pub fn terminate(&self) {
            unsafe {
                let _ = TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for JobGuard {
        fn drop(&mut self) {
            // KILL_ON_JOB_CLOSE 保证句柄关闭时整棵树被清理。
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    pub fn raw_handle_of(child: &tokio::process::Child) -> Option<RawHandle> {
        child.raw_handle()
    }
}

#[cfg(not(windows))]
mod imp {
    use std::io;
    use std::os::unix::io::RawHandle;

    pub struct JobGuard;

    impl JobGuard {
        pub fn new() -> io::Result<Self> {
            Ok(Self)
        }
        pub fn assign(&self, _process: RawHandle) -> io::Result<()> {
            Ok(())
        }
        pub fn terminate(&self) {}
    }

    pub fn raw_handle_of(_child: &tokio::process::Child) -> Option<RawHandle> {
        None
    }
}

pub use imp::{raw_handle_of, JobGuard};

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn assigning_a_real_process_does_not_error() {
        #[cfg(windows)]
        {
            let guard = JobGuard::new().expect("job object");
            let mut cmd = tokio::process::Command::new("cmd");
            crate::core::proc::hide_console_async(&mut cmd);
            let mut child = cmd
                .args(["/C", "timeout", "/T", "5", "/NOBREAK"])
                .stdout(std::process::Stdio::null())
                .spawn()
                .expect("spawn");
            let handle = raw_handle_of(&child).expect("handle");
            guard.assign(handle).expect("assign");
            guard.terminate();
            let status = tokio::time::timeout(std::time::Duration::from_secs(5), child.wait())
                .await
                .expect("terminated in time")
                .expect("wait");
            assert!(!status.success(), "被终止的进程不应成功退出");
        }
    }
}
