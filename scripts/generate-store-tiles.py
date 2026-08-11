#!/usr/bin/env python3
"""Generate complete MSIX / Store tile assets from the HonestSpend mark.

Fixes Microsoft Store 10.1.1.11 (On Device Tiles) risk by:
  • Unique branded tiles on solid brand background (not VS template / blank)
  • Correct pixel sizes for scale-100 and scale-200
  • Multi-size AppIcon.ico (not a 16×16 stub)
  • Wide tile with product name so Start tiles uniquely identify HonestSpend

Usage (from repo root):
  python scripts/generate-store-tiles.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
LOGO_DIR = ROOT / "store-assets" / "logo"
ASSETS = ROOT / "clients" / "HonestSpend.WinUI" / "Assets"

# Brand navy — matches logo blue panels; solid tiles read better on Start
BRAND_BG = (15, 39, 68, 255)  # #0F2744
BRAND_ACCENT = (27, 58, 107, 255)
WHITE = (255, 255, 255, 255)


def load_mark() -> Image.Image:
    for name in (
        "icon-cropped-transparent.png",
        "icon-300x300.png",
        "StoreLogo.png",
    ):
        p = LOGO_DIR / name
        if p.is_file():
            im = Image.open(p).convert("RGBA")
            # Prefer cropped transparent mark when available
            if name == "icon-cropped-transparent.png" or im.getbbox():
                return im
    raise SystemExit(f"No source mark found under {LOGO_DIR}")


def fit_on_canvas(
    mark: Image.Image,
    size: tuple[int, int],
    *,
    pad_ratio: float = 0.14,
    bg: tuple[int, int, int, int] = BRAND_BG,
) -> Image.Image:
    w, h = size
    canvas = Image.new("RGBA", (w, h), bg)
    max_w = int(w * (1 - 2 * pad_ratio))
    max_h = int(h * (1 - 2 * pad_ratio))
    mw, mh = mark.size
    scale = min(max_w / mw, max_h / mh)
    nw = max(1, int(round(mw * scale)))
    nh = max(1, int(round(mh * scale)))
    resized = mark.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (w - nw) // 2
    y = (h - nh) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


def wide_with_wordmark(mark: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Wide Start tile: mark left + HonestSpend wordmark (unique product identity)."""
    w, h = size
    canvas = Image.new("RGBA", (w, h), BRAND_BG)
    # Compact mark so wordmark fits fully at scale-100 (310×150)
    mark_box = int(h * 0.72)
    m = fit_on_canvas(mark, (mark_box, mark_box), pad_ratio=0.10, bg=(0, 0, 0, 0))
    mx = int(h * 0.08)
    my = (h - mark_box) // 2
    canvas.paste(m, (mx, my), m)

    draw = ImageDraw.Draw(canvas)
    text = "HonestSpend"
    # Prefer a clean system font; fall back to default
    font_paths = (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    )
    # Fit text into remaining width
    tx0 = mx + mark_box + int(h * 0.06)
    max_text_w = w - tx0 - int(h * 0.08)
    font = None
    for size_pt in (max(14, h // 4), max(12, h // 5), max(11, h // 6), 16, 14, 12):
        for path in font_paths:
            if not Path(path).is_file():
                continue
            try:
                candidate = ImageFont.truetype(path, size=size_pt)
            except OSError:
                continue
            bb = draw.textbbox((0, 0), text, font=candidate)
            if (bb[2] - bb[0]) <= max_text_w:
                font = candidate
                break
        if font is not None:
            break
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = tx0
    ty = (h - th) // 2 - bbox[1]
    pad_x, pad_y = 10, 6
    draw.rounded_rectangle(
        (tx - pad_x, ty - pad_y, min(w - 8, tx + tw + pad_x), ty + th + pad_y),
        radius=8,
        fill=BRAND_ACCENT,
    )
    draw.text((tx, ty), text, font=font, fill=WHITE)
    return canvas


def save_png(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG", optimize=True)
    print(f"  {path.relative_to(ROOT)}  {im.size[0]}x{im.size[1]}")


def write_ico(path: Path, sizes: list[int], mark: Image.Image) -> None:
    """Multi-resolution ICO with embedded PNG frames (Vista+)."""
    import struct
    from io import BytesIO

    images = [fit_on_canvas(mark, (s, s), pad_ratio=0.10).convert("RGBA") for s in sizes]
    entries: list[tuple[int, int, int, int]] = []
    payloads: list[bytes] = []
    offset = 6 + 16 * len(images)
    for im in images:
        buf = BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
        w, h = im.size
        entries.append((0 if w >= 256 else w, 0 if h >= 256 else h, len(data), offset))
        payloads.append(data)
        offset += len(data)
    with path.open("wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        for w, h, size, off in entries:
            f.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, size, off))
        for data in payloads:
            f.write(data)
    print(f"  {path.relative_to(ROOT)}  sizes={sizes}  ({path.stat().st_size} bytes)")


def main() -> None:
    mark = load_mark()
    # Ensure mark is tight crop
    bbox = mark.getbbox()
    if bbox:
        mark = mark.crop(bbox)

    print("Generating Store / MSIX tiles…")
    ASSETS.mkdir(parents=True, exist_ok=True)

    # --- Required VisualElements (scale-100 + scale-200) ---
    # Square150x150
    save_png(fit_on_canvas(mark, (150, 150)), ASSETS / "Square150x150Logo.scale-100.png")
    save_png(fit_on_canvas(mark, (300, 300)), ASSETS / "Square150x150Logo.scale-200.png")
    # also unscaled alias for tools that ignore qualifiers
    save_png(fit_on_canvas(mark, (150, 150)), ASSETS / "Square150x150Logo.png")

    # Square44x44
    save_png(fit_on_canvas(mark, (44, 44), pad_ratio=0.12), ASSETS / "Square44x44Logo.scale-100.png")
    save_png(fit_on_canvas(mark, (88, 88), pad_ratio=0.12), ASSETS / "Square44x44Logo.scale-200.png")
    save_png(fit_on_canvas(mark, (44, 44), pad_ratio=0.12), ASSETS / "Square44x44Logo.png")

    # Square71x71 (small tile)
    save_png(fit_on_canvas(mark, (71, 71)), ASSETS / "Square71x71Logo.scale-100.png")
    save_png(fit_on_canvas(mark, (142, 142)), ASSETS / "Square71x71Logo.scale-200.png")
    save_png(fit_on_canvas(mark, (71, 71)), ASSETS / "Square71x71Logo.png")

    # Wide 310x150 — wordmark makes tile uniquely "HonestSpend"
    save_png(wide_with_wordmark(mark, (310, 150)), ASSETS / "Wide310x150Logo.scale-100.png")
    save_png(wide_with_wordmark(mark, (620, 300)), ASSETS / "Wide310x150Logo.scale-200.png")
    save_png(wide_with_wordmark(mark, (310, 150)), ASSETS / "Wide310x150Logo.png")

    # StoreLogo (package Properties.Logo) — 50×50 scale-100
    save_png(fit_on_canvas(mark, (50, 50), pad_ratio=0.10), ASSETS / "StoreLogo.scale-100.png")
    save_png(fit_on_canvas(mark, (100, 100), pad_ratio=0.10), ASSETS / "StoreLogo.scale-200.png")
    save_png(fit_on_canvas(mark, (50, 50), pad_ratio=0.10), ASSETS / "StoreLogo.png")

    # Splash 620×300 (scale-200 standard) + scale-100 310×150
    splash_mark = mark
    save_png(
        fit_on_canvas(splash_mark, (310, 150), pad_ratio=0.28, bg=WHITE),
        ASSETS / "SplashScreen.scale-100.png",
    )
    save_png(
        fit_on_canvas(splash_mark, (620, 300), pad_ratio=0.28, bg=WHITE),
        ASSETS / "SplashScreen.scale-200.png",
    )
    save_png(
        fit_on_canvas(splash_mark, (620, 300), pad_ratio=0.28, bg=WHITE),
        ASSETS / "SplashScreen.png",
    )

    # Lock screen
    save_png(fit_on_canvas(mark, (24, 24), pad_ratio=0.08), ASSETS / "LockScreenLogo.scale-100.png")
    save_png(fit_on_canvas(mark, (48, 48), pad_ratio=0.08), ASSETS / "LockScreenLogo.scale-200.png")

    # Taskbar / unplated (must remain unique — not solid color blobs)
    save_png(
        fit_on_canvas(mark, (24, 24), pad_ratio=0.08, bg=(0, 0, 0, 0)),
        ASSETS / "Square44x44Logo.targetsize-24_altform-unplated.png",
    )
    save_png(
        fit_on_canvas(mark, (48, 48), pad_ratio=0.08, bg=(0, 0, 0, 0)),
        ASSETS / "Square44x44Logo.targetsize-48_altform-unplated.png",
    )
    save_png(
        fit_on_canvas(mark, (48, 48), pad_ratio=0.08, bg=(0, 0, 0, 0)),
        ASSETS / "Square44x44Logo.targetsize-48_altform-lightunplated.png",
    )

    # Multi-size ICO for ApplicationIcon / Explorer
    write_ico(ASSETS / "AppIcon.ico", [16, 24, 32, 48, 64, 128, 256], mark)

    # Partner Center listing copies
    save_png(fit_on_canvas(mark, (300, 300)), LOGO_DIR / "icon-300x300.png")
    save_png(fit_on_canvas(mark, (150, 150)), LOGO_DIR / "icon-150x150.png")
    save_png(fit_on_canvas(mark, (71, 71)), LOGO_DIR / "icon-71x71.png")
    save_png(fit_on_canvas(mark, (44, 44), pad_ratio=0.12), LOGO_DIR / "icon-44x44.png")
    save_png(fit_on_canvas(mark, (300, 300)), LOGO_DIR / "StoreLogo.png")

    print("Done. Rebuild MSIX after updating Package.appxmanifest BackgroundColor.")


if __name__ == "__main__":
    main()
