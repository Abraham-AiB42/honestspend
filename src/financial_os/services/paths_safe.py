"""Path and upload safety for local engine APIs."""

from __future__ import annotations

from pathlib import Path

from financial_os.config import settings

# Default max bank export size (CSV/OFX/PDF/xlsx)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def safe_filename(name: str | None, *, default: str = "upload.bin") -> str:
    """Strip directories / traversal from client-supplied filenames."""
    raw = (name or default).strip() or default
    base = Path(raw.replace("\\", "/")).name
    if not base or base in (".", ".."):
        return default
    # Windows reserved-ish cleanup
    base = base.replace("\x00", "")
    return base[:180] if len(base) > 180 else base


def resolve_under_data_dir(path: str | Path) -> Path:
    """Resolve path; must live under settings.data_dir (or be the data_dir itself)."""
    root = Path(settings.data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    try:
        p.relative_to(root)
    except ValueError as e:
        raise ValueError(f"Path must be under data directory ({root})") from e
    return p


def enforce_upload_size(content: bytes | bytearray, *, max_bytes: int = MAX_UPLOAD_BYTES) -> None:
    if len(content) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise ValueError(f"Upload too large (max {mb} MB)")
