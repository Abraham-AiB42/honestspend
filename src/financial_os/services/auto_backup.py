"""Background auto-backup while the engine is running."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger("lederring.auto_backup")

_stop = threading.Event()
_thread: threading.Thread | None = None


def start_auto_backup_loop(
    session_factory: Callable,
    *,
    check_seconds: int = 900,
) -> None:
    """Daemon loop: every check_seconds, maybe run scheduled backup."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def run() -> None:
        # first check after short delay so startup finishes
        time.sleep(5)
        while not _stop.is_set():
            try:
                session = session_factory()
                try:
                    from financial_os.services.backup import maybe_auto_backup

                    result = maybe_auto_backup(session)
                    if result and result.get("ok"):
                        session.commit()
                        name = (result.get("backup") or {}).get("name")
                        log.info("Auto-backup created: %s", name)
                    else:
                        session.rollback()
                except Exception:
                    session.rollback()
                    log.exception("Auto-backup check failed")
                finally:
                    session.close()
            except Exception:
                log.exception("Auto-backup session failed")
            _stop.wait(check_seconds)

    _thread = threading.Thread(target=run, name="lederring-auto-backup", daemon=True)
    _thread.start()
    log.info("Auto-backup loop started (every %ss)", check_seconds)


def stop_auto_backup_loop() -> None:
    _stop.set()
