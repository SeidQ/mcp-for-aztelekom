"""Mark evidence on a screenshot (red boxes + optional short labels) -> <name>_marked.png.

Usage:
  python src/mark_shot.py <input.png> "x1,y1,x2,y2[:label]" ["x1,y1,x2,y2[:label]" ...]
Coordinates are in screenshot pixels (portal screenshots are 1280x720).
Only the rows/fields that prove the result should be boxed; leave everything else unmarked.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

RED = (220, 0, 0)


def _font(size=15):
    for f in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def mark(src, boxes, dst=None):
    img = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(img)
    font = _font()
    for spec in boxes:
        coords, _, label = spec.partition(":")
        x1, y1, x2, y2 = (int(v) for v in coords.split(","))
        d.rectangle([x1, y1, x2, y2], outline=RED, width=3)
        if label:
            tw = d.textlength(label, font=font)
            tx, ty = max(0, x2 - tw - 8), max(0, y1 - 22)
            d.rectangle([tx - 4, ty - 2, tx + tw + 4, ty + 19], fill=RED)
            d.text((tx, ty), label, fill="white", font=font)
    dst = dst or os.path.splitext(src)[0] + "_marked.png"
    img.save(dst)
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    print(mark(sys.argv[1], sys.argv[2:]))
