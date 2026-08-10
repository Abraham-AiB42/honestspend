"""Crop white void from source logo; export transparent square sizes only.

Usage:
  python store-assets/logo/process_selected_logo.py [path-to-source.jpg]
Default source: %USERPROFILE%\\Downloads\\gkIiu.jpg
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DEFAULT_SRC = Path.home() / "Downloads" / "gkIiu.jpg"


def remove_near_white(im: Image.Image, thr: int = 245) -> Image.Image:
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if r >= thr and g >= thr and b >= thr:
                px[x, y] = (255, 255, 255, 0)
    return im


def fit_square(img: Image.Image, size: int, pad_ratio: float = 0.08) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    max_content = int(size * (1 - 2 * pad_ratio))
    iw, ih = img.size
    scale = min(max_content / iw, max_content / ih)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (size - nw) // 2
    y = (size - nh) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


def content_row_blocks(img: Image.Image) -> list[tuple[int, int]]:
    w, h = img.size
    rows = [
        sum(1 for x in range(w) if img.getpixel((x, y))[3] > 20) for y in range(h)
    ]
    blocks: list[tuple[int, int]] = []
    in_content = False
    start = 0
    for y, o in enumerate(rows):
        if o > w * 0.01:
            if not in_content:
                in_content = True
                start = y
        elif in_content:
            blocks.append((start, y - 1))
            in_content = False
    if in_content:
        blocks.append((start, h - 1))
    return blocks


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_file():
        raise SystemExit(f"Source not found: {src}")

    im = remove_near_white(Image.open(src))
    bbox = im.getbbox()
    if not bbox:
        raise SystemExit("no content after white removal")
    full = im.crop(bbox)
    full.save(OUT / "full-logo-cropped-transparent.png")

    blocks = content_row_blocks(full)
    cw, ch = full.size
    if len(blocks) >= 2:
        y0, y1 = blocks[0]
        band = full.crop((0, y0, cw, y1 + 1))
        ib = band.getbbox()
        icon = band.crop(ib) if ib else band
    else:
        icon = full.crop((0, 0, cw, int(ch * 0.55)))
        ib = icon.getbbox()
        if ib:
            icon = icon.crop(ib)
    icon.save(OUT / "icon-cropped-transparent.png")

    for size in (300, 150, 71):
        fit_square(full, size, 0.06).save(OUT / f"full-logo-{size}x{size}.png")
    for size in (300, 150, 71, 44):
        fit_square(icon, size, 0.10).save(OUT / f"icon-{size}x{size}.png")

    store = fit_square(icon, 300, 0.10)
    store.save(OUT / "StoreLogo.png")
    store.save(ROOT / "clients" / "HonestSpend.WinUI" / "Assets" / "StoreLogo.png")
    print("Transparent logos written to", OUT)


if __name__ == "__main__":
    main()
