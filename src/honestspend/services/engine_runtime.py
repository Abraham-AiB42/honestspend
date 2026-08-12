"""Self-contained CPython embeddable layout (Store / portable).

Do not start python.exe unless a versioned python3xx.dll sits beside it;
the stable-ABI stub python3.dll is not enough. Packaged processes cannot
load that DLL from LocalAppData.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

_VERSIONED_DLL = re.compile(r"^python3[0-9]+\.dll$", re.IGNORECASE)


def is_versioned_runtime_dll(name: str) -> bool:
    """True for python314.dll; false for python3.dll (stable-ABI stub)."""
    return bool(_VERSIONED_DLL.match(Path(name).name))


def _embed_dlls(python_dir: Path) -> list[Path]:
    if not python_dir.is_dir():
        return []
    return [
        p
        for p in python_dir.glob("python3*.dll")
        if p.is_file() and p.stat().st_size > 0 and is_versioned_runtime_dll(p.name)
    ]


def has_honestspend_package(engine_root: str | Path) -> bool:
    root = Path(engine_root)
    return (root / "python" / "Lib" / "site-packages" / "honestspend").is_dir() or (
        root / "src" / "honestspend"
    ).is_dir()


def is_runnable_embed(engine_root: str | Path) -> bool:
    """True when python.exe has a versioned python3xx.dll and honestspend is installed."""
    root = Path(engine_root)
    python_dir = root / "python"
    exe = python_dir / "python.exe"
    if not exe.is_file() or exe.stat().st_size <= 0:
        return False
    if not _embed_dlls(python_dir):
        return False
    return has_honestspend_package(root)


def resolve_embed_python(engine_root: str | Path) -> Path | None:
    if not is_runnable_embed(engine_root):
        return None
    return Path(engine_root) / "python" / "python.exe"


def zip_has_runnable_embed(zip_path: str | Path) -> bool:
    """Zip must contain python/python.exe and a versioned python/python3xx.dll."""
    has_exe = False
    has_dll = False
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/").rstrip("/")
            parts = norm.split("/")
            if len(parts) < 2 or parts[-2] != "python":
                continue
            name = parts[-1]
            if name.lower() == "python.exe" and info.file_size > 0:
                has_exe = True
            elif is_versioned_runtime_dll(name) and info.file_size > 0:
                has_dll = True
            if has_exe and has_dll:
                return True
    return False
