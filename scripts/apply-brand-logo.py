"""Apply selected HonestSpend mark across site + screenshot HTML."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    # Fix common mojibake from PowerShell Set-Content
    index = ROOT / "site" / "index.html"
    t = index.read_text(encoding="utf-8")
    for bad, good in {
        "\u00e2\u20ac\u201d": "\u2014",  # mojibake em dash variants handled below
        "â€™": "'",
        "â†’": "\u2192",
        "â€œ": '"',
        "â€\x9d": '"',
    }.items():
        t = t.replace(bad, good)
    # Common Windows-1252 misread of UTF-8 punctuation
    t = t.replace("â€”", "\u2014").replace("â€“", "\u2013").replace("â€¦", "...")
    index.write_text(t, encoding="utf-8")
    print("index app-logo count:", t.count("app-logo"), "mojibake:", "â" in t)

    priv = ROOT / "site" / "privacy" / "index.html"
    pt = priv.read_text(encoding="utf-8")
    if "favicon-32.png" not in pt:
        needle = '<link rel="icon" href="/assets/favicon.ico" sizes="any" />'
        insert = (
            needle
            + "\n  "
            + '<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png" />'
            + "\n  "
            + '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png" />'
        )
        pt = pt.replace(needle, insert)
        priv.write_text(pt, encoding="utf-8")
        print("privacy favicons updated")

    # Screenshot HTML titlebars
    logo_src = ROOT / "site" / "assets" / "logo-icon.png"
    html_dir = ROOT / "store-assets" / "html"
    shutil.copy(logo_src, html_dir / "logo-icon.png")
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
        print(name, "ok" if "logo-icon.png" in h else "missing logo")

    fav = ROOT / "site" / "assets" / "favicon.svg"
    if fav.exists():
        fav.unlink()
        print("removed old favicon.svg")

    # Recapture screenshots
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.is_file():
        edge = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    out_dir = ROOT / "store-assets" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "01-home",
        "02-import",
        "03-books-honesty",
        "04-sort-charges",
        "05-month-close",
    ):
        html = (html_dir / f"{name}.html").resolve()
        png = out_dir / f"{name}.png"
        uri = html.as_uri()
        subprocess.run(
            [
                str(edge),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=1366,768",
                f"--screenshot={png}",
                uri,
            ],
            check=False,
            capture_output=True,
        )
        print("screenshot", name, "bytes", png.stat().st_size if png.exists() else 0)

    print("done")


if __name__ == "__main__":
    main()
