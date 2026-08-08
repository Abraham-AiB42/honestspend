"""Filesystem helpers for tray PID and engine logs."""

from __future__ import annotations

import os
from pathlib import Path

from financial_os.config import settings


def data_dir() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Path(settings.data_dir)


def tray_pid_path() -> Path:
    return data_dir() / "tray.pid"


def engine_log_path() -> Path:
    return data_dir() / "engine.log"


def write_pid(path: Path, pid: int | None = None) -> None:
    path.write_text(str(pid or os.getpid()), encoding="utf-8")


def read_pid(path: Path) -> int | None:
    try:
        if not path.is_file():
            return None
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # Windows + Unix: signal 0 checks existence
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def clear_pid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
