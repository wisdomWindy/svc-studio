"""生成应用图标母版（1024×1024）。仅依赖 Pillow，可重复执行。

设计：圆角方块 + 品牌橙 + 白色 "S" + 底部三条声波线（SVC 的"声音"语义）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
ACCENT = (214, 106, 58, 255)
INK = (255, 255, 255, 255)
OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons" / "source.png"


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    plate = Image.new("RGBA", (SIZE, SIZE), ACCENT)
    canvas.paste(plate, (0, 0), rounded_mask(SIZE, 220))

    draw = ImageDraw.Draw(canvas)
    font = load_font(560)
    text = "S"
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(
        ((SIZE - tw) / 2 - box[0], (SIZE - th) / 2 - box[1] - 60),
        text,
        font=font,
        fill=INK,
    )

    for i, (w, alpha) in enumerate(((300, 235), (210, 160), (120, 90))):
        y = 800 + i * 22
        draw.rounded_rectangle(
            ((SIZE - w) / 2, y, (SIZE + w) / 2, y + 10),
            radius=5,
            fill=(255, 255, 255, alpha),
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, "PNG")
    print(f"written {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
