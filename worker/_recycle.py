import os
import struct
import sys

roots = [r"D:\$Recycle.Bin", r"C:\$Recycle.Bin"]
rows = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for sid in os.listdir(root):
        d = os.path.join(root, sid)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.startswith("$I"):
                continue
            meta = os.path.join(d, name)
            data = os.path.join(d, "$R" + name[2:])
            try:
                with open(meta, "rb") as f:
                    head = f.read(8)
                    if len(head) < 8:
                        continue
                    version = struct.unpack("<Q", head)[0]
                    if version == 2:
                        size = struct.unpack("<Q", f.read(8))[0]
                        f.read(8)
                        n = struct.unpack("<I", f.read(4))[0]
                        path = f.read(n * 2).decode("utf-16-le").rstrip("\x00")
                    else:
                        size = struct.unpack("<Q", f.read(8))[0]
                        path = ""
                        try:
                            f.seek(24)
                            raw = f.read()
                            path = raw.decode("utf-16-le", "ignore").split("\x00")[0]
                        except Exception:
                            pass
            except Exception:
                continue
            real = 0
            if os.path.isdir(data):
                for base, _, files in os.walk(data):
                    for fn in files:
                        try:
                            real += os.path.getsize(os.path.join(base, fn))
                        except OSError:
                            pass
            elif os.path.isfile(data):
                real = os.path.getsize(data)
            rows.append((real / 1024**3, path, data))

rows.sort(reverse=True)
for gb, path, data in rows[:25]:
    print(f"{gb:7.2f} GB  {path}")
