"""Local SQLite backup / restore for HonestSpend data."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from honestspend.config import settings


@dataclass
class BackupInfo:
    name: str
    path: Path
    size_bytes: int
    created: str  # ISO


def backups_dir() -> Path:
    d = settings.data_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_status() -> dict[str, Any]:
    db = settings.db_path
    size = db.stat().st_size if db.exists() else 0
    return {
        "data_dir": str(settings.data_dir),
        "db_path": str(db),
        "db_exists": db.exists(),
        "db_size_bytes": size,
        "db_size_mb": round(size / (1024 * 1024), 3) if size else 0,
        "backups_dir": str(backups_dir()),
        "backup_count": len(list_backups()),
    }


def list_backups() -> list[BackupInfo]:
    items: list[BackupInfo] = []
    for p in sorted(backups_dir().glob("ledger_*.db"), reverse=True):
        st = p.stat()
        created = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        items.append(BackupInfo(name=p.name, path=p, size_bytes=st.st_size, created=created))
    for p in sorted(backups_dir().glob("ledger_*.zip"), reverse=True):
        st = p.stat()
        created = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        items.append(BackupInfo(name=p.name, path=p, size_bytes=st.st_size, created=created))
    items.sort(key=lambda b: b.created, reverse=True)
    return items


def create_backup(*, as_zip: bool = True, note: str | None = None) -> dict[str, Any]:
    """Copy live DB into backups/. Optionally wrap in a zip with a small README."""
    src = settings.db_path
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    base = f"ledger_{stamp}"
    out_dir = backups_dir()

    # Copy via temporary file so readers keep a consistent snapshot on Windows
    tmp = out_dir / f".{base}.tmp.db"
    shutil.copy2(src, tmp)

    if as_zip:
        dest = out_dir / f"{base}.zip"
        # never clobber an existing backup name
        n = 0
        while dest.exists():
            n += 1
            dest = out_dir / f"{base}_{n}.zip"
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp, arcname="honestspend.db")
            meta = (
                f"HonestSpend backup\n"
                f"created_utc={datetime.now(timezone.utc).isoformat()}\n"
                f"source={src}\n"
                f"note={note or ''}\n"
            )
            zf.writestr("BACKUP.txt", meta)
        tmp.unlink(missing_ok=True)
    else:
        dest = out_dir / f"{base}.db"
        n = 0
        while dest.exists():
            n += 1
            dest = out_dir / f"{base}_{n}.db"
        tmp.replace(dest)

    st = dest.stat()
    return {
        "ok": True,
        "name": dest.name,
        "path": str(dest),
        "size_bytes": st.st_size,
        "created": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "format": "zip" if as_zip else "sqlite",
    }


def backup_path(name: str) -> Path:
    """Resolve a backup filename under backups/ (no path traversal)."""
    safe = Path(name).name
    if safe != name or ".." in name or "/" in name or "\\" in name:
        raise ValueError("Invalid backup name")
    if not (safe.startswith("ledger_") and (safe.endswith(".db") or safe.endswith(".zip"))):
        raise ValueError("Unknown backup pattern")
    path = backups_dir() / safe
    if not path.is_file():
        raise FileNotFoundError(f"Backup not found: {safe}")
    return path


def read_backup_bytes(name: str) -> tuple[bytes, str, str]:
    """Return (bytes, media_type, download_filename)."""
    path = backup_path(name)
    data = path.read_bytes()
    if path.suffix == ".zip":
        return data, "application/zip", path.name
    return data, "application/x-sqlite3", path.name


def zip_stream_from_live_db() -> BytesIO:
    """In-memory zip of current DB for immediate download (not persisted)."""
    src = settings.db_path
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, arcname="honestspend.db")
        zf.writestr(
            "BACKUP.txt",
            f"HonestSpend live snapshot\ncreated_utc={datetime.now(timezone.utc).isoformat()}\n",
        )
    buf.seek(0)
    return buf


def _next_db_path() -> Path:
    return settings.data_dir / "honestspend.db.next"


def _restore_flag_path() -> Path:
    return settings.data_dir / ".restore_pending"


def restore_pending() -> bool:
    return _next_db_path().is_file() and _restore_flag_path().is_file()


def _extract_backup_to(path: Path, dest_db: Path) -> None:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            db_member = next(
                (n for n in names if n.endswith("honestspend.db") or n.endswith(".db")),
                None,
            )
            if not db_member:
                raise ValueError("ZIP has no .db member")
            with zf.open(db_member) as src, open(dest_db, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        shutil.copy2(path, dest_db)


def restore_from_backup(name: str) -> dict[str, Any]:
    """
    Stage restore: write honestspend.db.next + .restore_pending.
    Live DB is NOT overwritten until process restart (apply_pending_restore).
    """
    path = backup_path(name)
    safety = create_backup(as_zip=True, note=f"pre-restore safety before {name}")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    next_db = _next_db_path()
    flag = _restore_flag_path()
    _extract_backup_to(path, next_db)
    flag.write_text(
        f"restored_from={name}\n"
        f"staged_at={datetime.now(timezone.utc).isoformat()}\n"
        f"safety={safety['name']}\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "staged": True,
        "requires_restart": True,
        "restored_from": name,
        "next_db": str(next_db),
        "safety_backup": safety["name"],
        "hint": (
            "Restore staged. Restart the HonestSpend engine (or WinUI) to apply. "
            "Writes continue on the old DB until restart."
        ),
    }


def apply_pending_restore() -> dict[str, Any] | None:
    """
    Called at engine startup: if .db.next + flag exist, replace live DB.
    Must run before heavy use of the live engine connection pool ideally
    at process start (init_db).
    """
    next_db = _next_db_path()
    flag = _restore_flag_path()
    if not next_db.is_file() or not flag.is_file():
        return None

    live = settings.db_path
    live.parent.mkdir(parents=True, exist_ok=True)
    bak = live.with_suffix(".db.pre_restore")
    meta = flag.read_text(encoding="utf-8") if flag.is_file() else ""

    if live.exists():
        try:
            shutil.copy2(live, bak)
        except OSError:
            pass
    try:
        shutil.copy2(next_db, live)
    except OSError:
        live.write_bytes(next_db.read_bytes())

    next_db.unlink(missing_ok=True)
    flag.unlink(missing_ok=True)

    return {
        "ok": True,
        "applied": True,
        "live_db": str(live),
        "pre_restore_copy": str(bak) if bak.exists() else None,
        "meta": meta,
    }


def restore_from_upload(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Save upload into backups/ then stage restore."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    lower = (filename or "").lower()
    if lower.endswith(".zip"):
        name = f"ledger_upload_{stamp}.zip"
    else:
        name = f"ledger_upload_{stamp}.db"
    dest = backups_dir() / name
    dest.write_bytes(file_bytes)
    return restore_from_backup(name)


def prune_backups(keep: int = 14) -> dict[str, Any]:
    """Delete oldest ledger_* backups beyond `keep` (zip + db mixed, by mtime)."""
    keep = max(1, int(keep))
    items = list_backups()
    removed: list[str] = []
    for b in items[keep:]:
        try:
            b.path.unlink(missing_ok=True)
            removed.append(b.name)
        except OSError:
            pass
    return {"kept": min(keep, len(items)), "removed": removed, "removed_count": len(removed)}


def schedule_status(session) -> dict[str, Any]:
    from honestspend.db import AppSettings

    row = session.get(AppSettings, 1) or AppSettings(id=1)
    last = getattr(row, "auto_backup_last_at", None)
    interval = int(getattr(row, "auto_backup_interval_hours", None) or 24)
    enabled = bool(getattr(row, "auto_backup_enabled", True))
    keep = int(getattr(row, "auto_backup_keep", None) or 14)
    due = False
    next_due = None
    if enabled:
        if last is None:
            due = True
        else:
            # naive UTC stored
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            elapsed_h = (now - last).total_seconds() / 3600.0
            due = elapsed_h >= interval
            from datetime import timedelta

            next_due = (last + timedelta(hours=interval)).isoformat() + "Z"
    return {
        "enabled": enabled,
        "interval_hours": interval,
        "keep": keep,
        "last_at": last.isoformat() + "Z" if last else None,
        "due_now": due,
        "next_due_at": next_due,
        "backups": db_status(),
    }


def maybe_auto_backup(session, *, force: bool = False) -> dict[str, Any] | None:
    """
    If schedule says due (or force), create a zip backup, prune, stamp last_at.
    Returns result dict or None if skipped.
    """
    from honestspend.db import AppSettings

    row = session.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        session.add(row)
        session.flush()

    st = schedule_status(session)
    if not force and (not st["enabled"] or not st["due_now"]):
        return None

    try:
        result = create_backup(as_zip=True, note="auto")
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e), "skipped": False}

    keep = int(getattr(row, "auto_backup_keep", None) or 14)
    pruned = prune_backups(keep)
    row.auto_backup_last_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.flush()
    return {
        "ok": True,
        "auto": True,
        "backup": result,
        "pruned": pruned,
        "last_at": row.auto_backup_last_at.isoformat() + "Z",
    }
