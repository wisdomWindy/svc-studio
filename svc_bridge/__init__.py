"""SVC Studio execution layer.

每个 job 一个进程：Rust 通过 stdin 下发控制指令，Python 通过 stdout 回传
JSON Lines 事件。stdout 只允许协议输出，任何库的 print 必须重定向到 stderr。
"""

__version__ = "0.1.0"
