"""Wire selected logo into marketing site HTML (UTF-8 safe)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_index() -> None:
    # Prefer clean tree version then patch
    p = ROOT / "site" / "index.html"
    t = p.read_text(encoding="utf-8")

    fav_old = '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml" />'
    fav_new = (
        '<link rel="icon" href="/assets/favicon.ico" sizes="any" />\n'
        '  <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png" />\n'
        '  <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16.png" />\n'
        '  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png" />'
    )
    if fav_old in t:
        t = t.replace(fav_old, fav_new)
    elif 'favicon.ico' not in t:
        t = t.replace(
            '<link rel="stylesheet" href="/styles.css" />',
            fav_new + '\n  <link rel="stylesheet" href="/styles.css" />',
        )

    t = t.replace(
        '<span class="brand-mark" aria-hidden="true">H</span>',
        '<img class="brand-mark" src="/assets/logo-icon.png" width="32" height="32" alt="" />',
    )

    # Normalize every app titlebar line/block to include logo
    t = re.sub(
        r'<div class="app-titlebar">[\s\S]*?</div>',
        (
            '<div class="app-titlebar">'
            '<span class="dots" aria-hidden="true">'
            '<span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>'
            "</span>"
            '<img class="app-logo" src="/assets/logo-icon.png" width="18" height="18" alt="" /> '
            "HonestSpend"
            "</div>"
        ),
        t,
        count=10,
    )

    p.write_text(t, encoding="utf-8")
    print(
        "index:",
        "logo-icon",
        t.count("logo-icon.png"),
        "brand-mark H left",
        t.count('brand-mark" aria-hidden="true">H'),
    )


def patch_privacy() -> None:
    p = ROOT / "site" / "privacy" / "index.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        '<span class="brand-mark" aria-hidden="true">H</span>',
        '<img class="brand-mark" src="/assets/logo-icon.png" width="32" height="32" alt="" />',
    )
    fav_old = '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml" />'
    fav_new = (
        '<link rel="icon" href="/assets/favicon.ico" sizes="any" />\n'
        '  <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png" />\n'
        '  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png" />'
    )
    if fav_old in t:
        t = t.replace(fav_old, fav_new)
    elif "favicon.ico" not in t:
        t = t.replace(
            '<link rel="stylesheet" href="/styles.css" />',
            fav_new + '\n  <link rel="stylesheet" href="/styles.css" />',
        )
    p.write_text(t, encoding="utf-8")
    print(
        "privacy:",
        "logo-icon",
        t.count("logo-icon.png"),
        "github",
        "github" in t.lower(),
        "mojibake",
        "â" in t or "�" in t,
    )


def patch_shots() -> None:
    import shutil

    html_dir = ROOT / "store-assets" / "html"
    shutil.copy(ROOT / "site" / "assets" / "logo-icon.png", html_dir / "logo-icon.png")
    css = html_dir / "_shot-base.css"
    ct = css.read_text(encoding="utf-8")
    if ".titlebar .app-logo" not in ct:
        css.write_text(
            ct
            + "\n.titlebar .app-logo { width: 20px; height: 20px; object-fit: contain; border-radius: 3px; }\n",
            encoding="utf-8",
        )
    for name in (
        "01-home",
        "02-import",
        "03-books-honesty",
        "04-sort-charges",
        "05-month-close",
    ):
        fp = html_dir / f"{name}.html"
        h = fp.read_text(encoding="utf-8")
        h = h.replace(
            '<span class="mark">H</span>',
            '<img class="app-logo" src="logo-icon.png" width="20" height="20" alt="" />',
        )
        fp.write_text(h, encoding="utf-8")


def recapture() -> None:
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.is_file():
        edge = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    html_dir = ROOT / "store-assets" / "html"
    out_dir = ROOT / "store-assets" / "screenshots"
    for name in (
        "01-home",
        "02-import",
        "03-books-honesty",
        "04-sort-charges",
        "05-month-close",
    ):
        html = (html_dir / f"{name}.html").resolve()
        png = out_dir / f"{name}.png"
        subprocess.run(
            [
                str(edge),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=1366,768",
                f"--screenshot={png}",
                html.as_uri(),
            ],
            check=False,
            capture_output=True,
        )
        print("shot", name, png.stat().st_size if png.exists() else 0)


def main() -> None:
    # Ensure we start from last committed clean HTML for privacy if corrupted
    priv = ROOT / "site" / "privacy" / "index.html"
    if "�" in priv.read_text(encoding="utf-8", errors="replace") or "â" in priv.read_text(
        encoding="utf-8", errors="replace"
    ):
        subprocess.run(
            ["git", "checkout", "HEAD", "--", "site/privacy/index.html"],
            cwd=ROOT,
            check=False,
        )
        print("restored privacy from git")

    patch_index()
    patch_privacy()
    patch_shots()
    recapture()
    fav = ROOT / "site" / "assets" / "favicon.svg"
    if fav.exists():
        fav.unlink()
    print("done")


if __name__ == "__main__":
    main()
