"""Crop white void from gkIiu logo and fit into Windows square sizes (no stretch)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\abrah\Downloads\gkIiu.jpg")
OUT = Path(__file__).resolve().parent / "selected"
ROOT = Path(__file__).resolve().parents[2]


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
    """Scale content to fit inside size x size with transparent padding (never stretch)."""
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


def with_white_bg(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def content_row_blocks(img: Image.Image) -> list[tuple[int, int]]:
    w, h = img.size
    rows = []
    for y in range(h):
        opaque = sum(1 for x in range(w) if img.getpixel((x, y))[3] > 20)
        rows.append(opaque)
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
    OUT.mkdir(parents=True, exist_ok=True)
    im = remove_near_white(Image.open(SRC))
    bbox = im.getbbox()
    if not bbox:
        raise SystemExit("no content found after white removal")
    print("content bbox:", bbox)
    full = im.crop(bbox)
    full.save(OUT / "full-logo-cropped-transparent.png")

    # Icon = first vertical content block (mark above wordmark)
    blocks = content_row_blocks(full)
    print("row blocks:", blocks)
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
    print("icon size:", icon.size, "full size:", full.size)

    # Full logo (icon + wordmark) — fits letterboxed in square
    for size in (300, 150, 71):
        sq = fit_square(full, size, pad_ratio=0.06)
        sq.save(OUT / f"full-logo-{size}x{size}.png")
        with_white_bg(sq).save(OUT / f"full-logo-whitebg-{size}x{size}.png")

    # Icon-only (recommended for Store square + package tiles)
    for size in (300, 150, 71, 44):
        sq = fit_square(icon, size, pad_ratio=0.10)
        sq.save(OUT / f"icon-{size}x{size}.png")
        if size in (300, 150, 71):
            with_white_bg(sq).save(OUT / f"icon-whitebg-{size}x{size}.png")

    # Primary Store / app package assets (icon, white bg, 300)
    primary300 = with_white_bg(fit_square(icon, 300, pad_ratio=0.10))
    primary300.save(OUT / "StoreLogo-300.png")
    primary300.save(OUT / "logo-300x300.png")
    with_white_bg(fit_square(icon, 150, pad_ratio=0.10)).save(OUT / "logo-150x150.png")
    with_white_bg(fit_square(icon, 71, pad_ratio=0.10)).save(OUT / "logo-71x71.png")

    # Copy into logo folder + WinUI Assets
    primary300.save(ROOT / "store-assets" / "logo" / "StoreLogo-300.png")
    primary300.save(ROOT / "store-assets" / "logo" / "StoreLogo.png")
    primary300.save(ROOT / "clients" / "HonestSpend.WinUI" / "Assets" / "StoreLogo.png")
    with_white_bg(fit_square(icon, 512, pad_ratio=0.10)).save(
        ROOT / "store-assets" / "logo" / "StoreLogo-512.png"
    )

    print("DONE")
    print("Primary 300x300 (icon, white void removed):", OUT / "logo-300x300.png")


if __name__ == "__main__":
    main()
