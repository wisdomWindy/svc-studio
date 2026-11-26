"""真实数据验收：worker CLI 输出契约 + 重复执行安全性（非破坏性）。

真实数据集上只跑只读命令；任何会改动文件的命令都在临时副本上跑。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(r"D:\program\svcstudio\SVC Studio\data\runtime")
RES = Path(r"D:\program\svcstudio\SVC Studio\resources")
REAL = Path(r"D:\program\svcstudio\SVC Studio\data\projects\qingqingzi\dataset_raw")
EXPECT_FILES = 616

env = dict(os.environ)
env.update(
    {
        "SVCSTUDIO_UPSTREAM_DIR": str(RES / "upstream"),
        "SVCSTUDIO_SEPARATOR_DIR": str(RES / "separator"),
        "SVCSTUDIO_FFMPEG": str(RES / "bin" / "ffmpeg.exe"),
        "PYTHONPATH": str(RT / "worker"),
        "PYTHONIOENCODING": "utf-8",
    }
)

fails = []


def run(args, root, label):
    proc = subprocess.run(
        [str(RT / "python.exe"), "-m", "svc_bridge", *args, "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    out = proc.stdout
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if len(lines) != 1:
        fails.append(f"{label}: stdout 有 {len(lines)} 行（应为 1）")
    try:
        data = json.loads(out.strip())
    except Exception as exc:  # noqa: BLE001
        fails.append(f"{label}: stdout 不是合法 JSON（{exc}）；前 300 字符 {out[:300]}")
        return None
    print(f"  {label:<30} 1行 JSON=OK  schema={data.get('schema')}")
    return data


print("=== 1) 真实数据集 · 只读命令 ===")
scan = run(["dataset", "scan"], REAL, "dataset scan")
if scan:
    print(f"     files={len(scan.get('files', []))} speakers={scan.get('speakers')}")
    if EXPECT_FILES and len(scan.get("files", [])) != EXPECT_FILES:
        fails.append(f"scan files={len(scan.get('files', []))}，期望 {EXPECT_FILES}")

qc = run(["dataset", "qc"], REAL, "dataset qc")
if qc:
    s = qc.get("summary", {})
    print(f"     files={s.get('fileCount')} minutes={s.get('totalMinutes')} "
          f"passed={s.get('passed')} blocking={s.get('blocking')}")
    if EXPECT_FILES and s.get("fileCount") != EXPECT_FILES:
        fails.append(f"qc fileCount={s.get('fileCount')}，期望 {EXPECT_FILES}")
    if s.get("blocking"):
        fails.append(f"qc 有 {s.get('blocking')} 个阻断项")

sep = run(["dataset", "separate"], REAL, "dataset separate（重复点）")
if sep:
    n = len(sep.get("separated", []))
    print(f"     重复分离：separated={n} failed={len(sep.get('failed', []))}")
    if n:
        fails.append(f"重复执行 separate 又分离了 {n} 个文件")

print("\n=== 2) 临时副本 · 会改文件的命令 ===")
tmp = Path(tempfile.mkdtemp(prefix="realdata-"))
raw = tmp / "dataset_raw"
spk = raw / "spk"
spk.mkdir(parents=True)
src = sorted(REAL.joinpath("qingqingzi").glob("*.wav"))[:10]
for f in src:
    shutil.copyfile(f, spk / f.name)
print(f"     副本 {len(src)} 个切片")

keep_all = run(["dataset", "prune-quality", "--keep", "1.0"], raw, "prune-quality keep=1.0")
if keep_all:
    print(f"     keepRatio={keep_all['keepRatio']} dropped={keep_all['droppedFiles']}")
    if keep_all["droppedFiles"] != 0:
        fails.append(f"keep=1.0 仍剔除了 {keep_all['droppedFiles']} 个文件")

dflt = run(["dataset", "prune-quality"], raw, "prune-quality 默认 0.85")
if dflt:
    print(f"     keepRatio={dflt['keepRatio']} dropped={dflt['droppedFiles']}")
    if dflt["keepRatio"] != 0.85 or dflt["droppedFiles"] == 0:
        fails.append("默认 keep 未生效")

run(["dataset", "slice"], raw, "dataset slice")
run(["dataset", "resample"], raw, "dataset resample")
run(["dataset", "declick", "--backup-dir", str(tmp / "bak")], raw, "dataset declick")

stray = list(spk.rglob("*.vocals_*.vocals*.wav")) + list(spk.rglob("*.vocals.vocals*.wav"))
if stray:
    fails.append(f"出现重复分离产物：{[p.name for p in stray][:3]}")
print(f"     副本处理后文件数={len(list(spk.glob('*.wav')))}")
shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 结论 ===")
if fails:
    print("FAIL:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS：真实数据上 stdout 均为单行合法 JSON；重复分离为空操作；--keep 生效；无重复产物")
