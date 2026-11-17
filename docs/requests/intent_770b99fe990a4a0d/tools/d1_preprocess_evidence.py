"""D1 证据：用 fake upstream 跑通 worker 的三步预处理链路。

不是"假装跑过"——这一步真实执行了：
  1. svc_bridge CLI 的参数解析与三步编排
  2. 每步独立子进程 + cwd=项目根
  3. 产物计数、差异清单、manifest 落盘
只把上游脚本替换成行为等价的假脚本（真实上游需要 torch 与权重）。
"""

from __future__ import annotations

import array
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # voice-generator/
EVIDENCE = ROOT / "docs/requests/intent_770b99fe990a4a0d/evidence/D1"


def write_wav(path: Path, seconds: float = 1.0, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = array.array("h")
    for i in range(int(seconds * sample_rate)):
        samples.append(int(12000 * math.sin(2 * math.pi * 220 * i / sample_rate)))
    with path.open("wb") as fh:
        import wave

        with wave.open(fh, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(samples.tobytes())


FAKE_SCRIPTS = {
    "resample.py": r'''
import sys, pathlib, shutil
root = pathlib.Path.cwd()
raw = root / "dataset_raw"
out = root / "dataset/44k"
for wav in raw.rglob("*.wav"):
    dst = out / wav.parent.name / wav.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wav, dst)
print("resampled", len(list(out.rglob('*.wav'))))
''',
    "preprocess_flist_config.py": r'''
import json, pathlib
root = pathlib.Path.cwd()
cfg = root / "configs/config.json"
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps({"data": {"sampling_rate": 44100, "spk": {"spk": 0}}}, indent=2), encoding="utf-8")
(root / "filelists").mkdir(exist_ok=True)
print("config written")
''',
    "preprocess_hubert_f0.py": r'''
import pathlib
root = pathlib.Path.cwd()
count = 0
for wav in (root / "dataset/44k").rglob("*.wav"):
    for suffix in (".soft.pt", ".f0.npy", ".spec.pt"):
        (wav.with_name(wav.stem + suffix)).write_bytes(b"fake")
    count += 1
print("features", count)
''',
}


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="svc-d1-"))
    project = work / "proj"
    upstream = work / "upstream"
    upstream.mkdir(parents=True)
    for name, body in FAKE_SCRIPTS.items():
        (upstream / name).write_text(body, encoding="utf-8")

    write_wav(project / "dataset_raw/spk/a.wav", 1.2)
    write_wav(project / "dataset_raw/spk/b.wav", 0.8)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "worker")
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        "-m",
        "svc_bridge",
        "preprocess",
        "--project",
        str(project),
        "--upstream",
        str(upstream),
        "--encoder",
        "vec768l12",
        "--f0",
        "rmvpe",
        "--manifest",
        str(project / "preprocess_manifest.json"),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    manifest_path = project / "preprocess_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    result = {
        "exitCode": proc.returncode,
        "stderrTail": (proc.stderr or "")[-1500:],
        "events": events[-8:],
        "manifest": manifest,
        "products": {
            "wav": len(list((project / "dataset/44k").rglob("*.wav"))),
            "soft": len(list((project / "dataset/44k").rglob("*.soft.pt"))),
            "f0": len(list((project / "dataset/44k").rglob("*.f0.npy"))),
            "spec": len(list((project / "dataset/44k").rglob("*.spec.pt"))),
        },
        "configExists": (project / "configs/config.json").is_file(),
        "command": " ".join(cmd),
    }
    (EVIDENCE / "d1-preprocess.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: result[k] for k in ("exitCode", "products", "configExists")}, ensure_ascii=False))
    if proc.returncode != 0:
        print("STDERR:", (proc.stderr or "")[-1200:])
        print("EVENTS:", [e.get("message") for e in events][-5:])
    shutil.rmtree(work, ignore_errors=True)
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
