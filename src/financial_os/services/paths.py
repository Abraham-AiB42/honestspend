"""Data directory discovery — home default + OneDrive-friendly candidates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from financial_os.config import settings


def _onedrive_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        raw = os.environ.get(key)
        if not raw:
            continue
        p = Path(raw).expanduser()
        try:
            key_s = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key_s = str(p)
        if key_s in seen:
            continue
        seen.add(key_s)
        roots.append(p)
    # Common Windows paths if env missing
    home = Path.home()
    for name in ("OneDrive", "OneDrive - Personal"):
        p = home / name
        if p.is_dir():
            key_s = str(p.resolve())
            if key_s not in seen:
                seen.add(key_s)
                roots.append(p)
    return roots


def data_path_info() -> dict[str, Any]:
    default = Path.home() / ".financial-os"
    current = Path(settings.data_dir)
    candidates: list[dict[str, str]] = [
        {
            "label": "Local profile (default)",
            "path": str(default),
            "kind": "home",
        }
    ]
    for od in _onedrive_roots():
        cand = od / "Floatpile" / "data"
        candidates.append(
            {
                "label": f"OneDrive · {od.name}",
                "path": str(cand),
                "kind": "onedrive",
            }
        )
        # also Documents under OneDrive if present
        docs = od / "Documents" / "Floatpile"
        candidates.append(
            {
                "label": f"OneDrive Documents · {od.name}",
                "path": str(docs),
                "kind": "onedrive_docs",
            }
        )

    return {
        "current": str(current),
        "default": str(default),
        "env_var": "FOS_DATA_DIR",
        "candidates": candidates,
        "db_path": str(settings.db_path),
        "hint": (
            "Set FOS_DATA_DIR before starting the engine (or in WinUI Settings). "
            "Relocating an existing DB: backup/export first, set the new path, restore or copy financial_os.db."
        ),
    }
