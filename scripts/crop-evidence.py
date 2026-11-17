"""把桌面截图裁成窗口区域，作为 M0 窗口证据（避免把无关桌面内容带进仓库）。"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
box = tuple(int(v) for v in sys.argv[3:7])  # left, top, right, bottom（预览图坐标系）

img = Image.open(src)
cropped = img.crop(box)
dst.parent.mkdir(parents=True, exist_ok=True)
cropped.save(dst, "PNG")
print(f"{dst} {cropped.size}")
