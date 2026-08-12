"""Data directory discovery — home default + cloud-folder candidates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from honestspend.config import settings


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
            if key_s in seen:
                continue
            seen.add(key_s)
            roots.append(p)
    return roots


def _dropbox_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    for name in ("Dropbox", "Dropbox (Personal)", "Dropbox (Business)"):
        p = home / name
        if p.is_dir():
            roots.append(p)
    # Dropbox info.json (Windows/macOS)
    for base in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Dropbox" / "info.json",
        home / ".dropbox" / "info.json",
    ):
        if not base.is_file():
            continue
        try:
            import json

            data = json.loads(base.read_text(encoding="utf-8"))
            for key in ("personal", "business"):
                entry = data.get(key) or {}
                path = entry.get("path")
                if path:
                    p = Path(path)
                    if p.is_dir() and p not in roots:
                        roots.append(p)
        except Exception:
            pass
    return roots


def _icloud_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    # Windows iCloud Drive
    for p in (
        home / "iCloudDrive",
        home / "iCloud Drive",
        Path(os.environ.get("USERPROFILE", str(home))) / "iCloudDrive",
    ):
        if p.is_dir():
            roots.append(p)
    return roots


def data_path_info() -> dict[str, Any]:
    from honestspend.config import default_data_dir

    default = default_data_dir()
    current = Path(settings.data_dir)
    candidates: list[dict[str, str]] = [
        {
            "label": "This PC only (default)",
            "path": str(default),
            "kind": "local",
        }
    ]
    for od in _onedrive_roots():
        cand = od / "HonestSpend" / "data"
        candidates.append(
            {
                "label": f"OneDrive · {od.name}",
                "path": str(cand),
                "kind": "onedrive",
            }
        )
        docs = od / "Documents" / "HonestSpend"
        candidates.append(
            {
                "label": f"OneDrive Documents · {od.name}",
                "path": str(docs),
                "kind": "onedrive_docs",
            }
        )
    for dbx in _dropbox_roots():
        cand = dbx / "HonestSpend" / "data"
        candidates.append(
            {
                "label": f"Dropbox · {dbx.name}",
                "path": str(cand),
                "kind": "dropbox",
            }
        )
    for ic in _icloud_roots():
        cand = ic / "HonestSpend" / "data"
        candidates.append(
            {
                "label": f"iCloud Drive · {ic.name}",
                "path": str(cand),
                "kind": "icloud",
            }
        )

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for c in candidates:
        key = c["path"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    return {
        "current": str(current),
        "default": str(default),
        "env_var": "FOS_DATA_DIR",
        "candidates": unique,
        "db_path": str(settings.db_path),
        "db_exists": settings.db_path.is_file(),
        "warning": (
            "Cloud folders sync live files. Open HonestSpend on one device at a time while writing. "
            "For handoff without conflict risk, use encrypted backups after setup."
        ),
        "hint": (
            "Set FOS_DATA_DIR before starting the engine (or choose Storage in Get started / Settings). "
            "Relocating: copy honestspend.db, set the new path, restart engine."
        ),
    }


def apply_storage_choice(
    *,
    kind: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Validate storage choice for setup payload (client applies FOS_DATA_DIR)."""
    kind_l = (kind or "local").strip().lower()
    info = data_path_info()
    if kind_l in ("local", "home", "default"):
        chosen = info["default"]
        kind_l = "local"
    elif kind_l == "custom":
        if not path or not str(path).strip():
            raise ValueError("Custom storage requires a folder path.")
        chosen = str(Path(path).expanduser())
    else:
        # Match kind to a candidate or accept explicit path
        if path and str(path).strip():
            chosen = str(Path(path).expanduser())
        else:
            match = next((c for c in info["candidates"] if c["kind"] == kind_l), None)
            if not match:
                raise ValueError(f"Unknown storage kind: {kind}")
            chosen = match["path"]
            kind_l = match["kind"]

    p = Path(chosen)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create data folder: {e}") from e

    return {
        "kind": kind_l,
        "path": str(p),
        "warning": info["warning"],
        "client_must_set_env": "FOS_DATA_DIR",
        "restart_engine": True,
    }
