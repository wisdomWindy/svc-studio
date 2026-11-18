"""进程退出码契约（spec §10）。Rust 侧据此区分「可重试」与「需修配置」。"""

OK = 0
USAGE = 2
ENV = 10
INPUT = 11
ASSET = 12
PREPROCESS = 13
TRAIN = 14
INFER = 15
CANCELLED = 30
CRASH = 70
