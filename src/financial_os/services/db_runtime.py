"""Process-wide DB open/close with optional at-rest encryption."""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import sessionmaker

log = logging.getLogger("honestspend.db_runtime")

_lock = threading.RLock()
_engine = None
_SessionLocal = None
_unlocked = False
_session_dek: bytes | None = None  # held only while process has unsealed books


def is_unlocked() -> bool:
    return _unlocked and _engine is not None


def session_factory():
    return _SessionLocal


def get_engine():
    return _engine


def get_session_dek() -> bytes | None:
    return _session_dek


def status() -> dict[str, Any]:
    from financial_os.services import db_crypto as dc

    st = dc.status()
    st["process_unlocked"] = is_unlocked()
    st["has_session_dek"] = _session_dek is not None
    return st


def boot() -> dict[str, Any]:
    """Open DB if plaintext available; otherwise remain locked."""
    global _engine, _SessionLocal, _unlocked, _session_dek
    with _lock:
        from financial_os.db import init_db, make_engine
        from financial_os.services import db_crypto as dc

        if dc.needs_unlock():
            _dispose_unlocked()
            _unlocked = False
            _session_dek = None
            log.info("Database sealed — waiting for unlock")
            return {"ok": True, "unlocked": False, "needs_unlock": True}

        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
        init_db(_engine)
        _unlocked = True
        # DEK only set on explicit unlock/enable
        log.info("Database open (plaintext ready)")
        return {"ok": True, "unlocked": True, "needs_unlock": False}


def _dispose_unlocked() -> None:
    global _engine, _SessionLocal, _unlocked
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _SessionLocal = None
    _unlocked = False


def unlock(*, secret: str | None = None, dek_b64: str | None = None) -> dict[str, Any]:
    global _engine, _SessionLocal, _unlocked, _session_dek
    with _lock:
        from financial_os.db import init_db, make_engine
        from financial_os.services import db_crypto as dc

        if not dc.encryption_enabled():
            # No encryption — normal boot
            return boot()

        dek = dc.resolve_dek(secret=secret, dek_b64=dek_b64)
        if dc.sealed_present() and not dc.plaintext_present():
            dc.unseal_database(dek)
        elif not dc.plaintext_present():
            raise ValueError("No sealed or plaintext database found")

        _dispose_unlocked()
        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
        init_db(_engine)
        _unlocked = True
        _session_dek = dek
        return {"ok": True, "unlocked": True, "process_unlocked": True, **dc.status()}


def lock_and_seal() -> dict[str, Any]:
    """Seal books and drop engine. No-op if encryption disabled."""
    global _engine, _SessionLocal, _unlocked, _session_dek
    with _lock:
        import gc
        import time

        from financial_os.services import db_crypto as dc

        if not dc.encryption_enabled():
            return {"ok": True, "sealed": False, "reason": "encryption_disabled"}

        dek = _session_dek
        if dek is None and dc.plaintext_present():
            raise ValueError(
                "Cannot seal: session has no DEK. Unlock with PIN once this session, then lock."
            )
        if dek is None and dc.sealed_present():
            _dispose_unlocked()
            return {"ok": True, "sealed": True, "already_sealed": True}

        # Flush engine before reading file (Windows holds locks until dispose + GC)
        _dispose_unlocked()
        gc.collect()
        time.sleep(0.1)
        if dc.plaintext_present() and dek is not None:
            result = dc.seal_database(dek)
        else:
            result = {"ok": True, "already_sealed": True}
        _session_dek = None
        _unlocked = False
        return {"ok": True, "sealed": True, **result}


def enable(
    *,
    secret: str | None = None,
    dek_b64: str | None = None,
    mode_hint: str = "pin",
    wrap: str = "password",
) -> dict[str, Any]:
    """Enable encryption while DB is open (plaintext). Keeps session unlocked."""
    global _session_dek, _engine, _SessionLocal, _unlocked
    with _lock:
        from financial_os.services import db_crypto as dc

        if not is_unlocked():
            # try boot first
            boot()
        if not is_unlocked():
            raise ValueError("Database must be open before enabling encryption")

        import base64

        dek = None
        if dek_b64:
            dek = base64.b64decode(dek_b64)
        out = dc.enable_encryption(
            secret=secret,
            dek=dek,
            mode_hint=mode_hint,
            wrap=wrap,
            seal_now=False,
        )
        _session_dek = base64.b64decode(out["dek_b64"])
        return out


def disable(*, secret: str | None = None, dek_b64: str | None = None) -> dict[str, Any]:
    global _session_dek, _engine, _SessionLocal, _unlocked
    with _lock:
        from financial_os.services import db_crypto as dc

        if not is_unlocked() and dc.needs_unlock():
            unlock(secret=secret, dek_b64=dek_b64)
        dek = _session_dek
        if dek is None and (secret or dek_b64):
            dek = dc.resolve_dek(secret=secret, dek_b64=dek_b64)
        out = dc.disable_encryption(secret=secret, dek=dek)
        if not out.get("enabled"):
            _session_dek = None
            # re-open plaintext after disable
            boot()
        return out


def require_unlocked() -> None:
    if not is_unlocked():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=423,
            detail="Database is locked. Unlock with your PIN/password or Windows Hello.",
        )
