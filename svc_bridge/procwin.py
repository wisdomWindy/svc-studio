"""进程相关的 Windows 细节。

控制台子进程（ffmpeg / taskkill / 上游 python 脚本）默认会弹黑框，
这里统一提供 `creationflags`，所有 subprocess 调用都必须带上。
"""

from __future__ import annotations

import os
import subprocess

# subprocess.CREATE_NO_WINDOW 在 Windows 上存在；其他平台没有这个常量。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def no_window_kwargs() -> dict[str, int]:
    """给 subprocess.run / Popen 用的 kwargs；非 Windows 返回空字典。"""
    if os.name == "nt":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}
