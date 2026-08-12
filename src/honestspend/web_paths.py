"""Locate glance / Plaid Link HTML for both repo and Store embed layouts."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_web_dir(*, start: Path | None = None) -> Path | None:
    """Return the first directory that contains plaid-link.html.

    Dev: <repo>/web. Store embed: engine/web (not python/Lib/web).
    """
    roots: list[Path] = []
    origin = (start or Path(__file__).resolve()).resolve()
    if origin.is_file():
        origin = origin.parent
    roots.append(origin)
    roots.extend(origin.parents)
    try:
        exe = Path(sys.executable).resolve()
        roots.append(exe.parent)
        roots.append(exe.parent.parent)
    except Exception:
        pass

    seen: set[Path] = set()
    for root in roots:
        cand = (root / "web").resolve()
        if cand in seen:
            continue
        seen.add(cand)
        if (cand / "plaid-link.html").is_file():
            return cand
    return None
