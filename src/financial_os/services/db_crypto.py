"""At-rest encryption for the live ledger (AES-256-GCM).

When app lock is enabled, books are sealed to ``financial_os.db.sealed`` and the
plaintext ``financial_os.db`` is removed. Unlock derives/unwraps a DEK and
restores plaintext for the engine process. Not multi-writer-safe (same as
unencrypted cloud folders).

Key hierarchy:
  user secret (PIN/password) → KEK (PBKDF2) → wrap DEK
  or client supplies DEK (Windows Hello / vault)

``crypto.json`` holds public metadata + wrapped DEK (PIN/password). Never the
raw DEK. Recovery: without secret/DEK the sealed blob is unrecoverable — disable
encryption only after unlock.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from financial_os.config import settings

log = logging.getLogger("honestspend.db_crypto")

CRYPTO_VERSION = 1
SEAL_MAGIC = b"HSDB1"
WRAP_MAGIC = b"HSWK1"
KDF_ITERATIONS = 200_000
DEK_LEN = 32


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        return AESGCM, PBKDF2HMAC, hashes
    except ImportError as e:
        raise RuntimeError(
            "DB encryption requires the 'cryptography' package (already a dependency)."
        ) from e


def crypto_meta_path() -> Path:
    return settings.data_dir / "crypto.json"


def sealed_db_path() -> Path:
    return settings.data_dir / "financial_os.db.sealed"


def plaintext_db_path() -> Path:
    return settings.db_path


def load_meta() -> dict[str, Any] | None:
    p = crypto_meta_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("Could not read crypto.json: %s", e)
        return None


def save_meta(data: dict[str, Any]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = crypto_meta_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(crypto_meta_path())
    try:
        if os.name != "nt":
            os.chmod(crypto_meta_path(), 0o600)
    except Exception:
        pass


def encryption_enabled() -> bool:
    meta = load_meta()
    return bool(meta and meta.get("enabled"))


def plaintext_present() -> bool:
    """True only if a usable SQLite plaintext DB exists (not empty/poisoned)."""
    p = plaintext_db_path()
    if not p.is_file() or p.stat().st_size < 16:
        return False
    try:
        with open(p, "rb") as f:
            head = f.read(16)
        return head.startswith(b"SQLite format 3")
    except OSError:
        return False


def sealed_present() -> bool:
    p = sealed_db_path()
    return p.is_file() and p.stat().st_size > 0


def needs_unlock() -> bool:
    """True when encryption is on — boot must never silent-open (even leftover plaintext).

    Crash/kill can leave plaintext on disk; that must still require PIN/DEK so the
    process recovers a session DEK (and can re-seal on exit).
    """
    if not encryption_enabled():
        return False
    # Sealed-only classic path, or plaintext leftover after failed seal
    return sealed_present() or plaintext_present()


def refuse_offline_open_if_encrypted() -> None:
    """Raise if CLI/tray must not open SQLite outside db_runtime unlock.

    Prevents creating an empty financial_os.db next to a real .sealed vault.
    """
    if not encryption_enabled():
        return
    if sealed_present() or plaintext_present() or crypto_meta_path().is_file():
        raise RuntimeError(
            "Books are encrypted. Start the HonestSpend app and unlock with your PIN/password "
            "(or Windows Hello), then retry. Offline CLI/tray must not open a sealed vault."
        )


def plaintext_is_usable_sqlite() -> bool:
    return plaintext_present()


def prefer_unseal_over_plaintext() -> bool:
    """True when sealed exists and leftover plaintext looks empty/stale/corrupt."""
    if not sealed_present():
        return False
    if not plaintext_present():
        return True
    try:
        plain = plaintext_db_path()
        sealed = sealed_db_path()
        # Empty or tiny file → prefer sealed
        if plain.stat().st_size < 4096:
            return True
        # Sealed newer than plaintext → prefer sealed (seal succeeded after open)
        if sealed.stat().st_mtime >= plain.stat().st_mtime:
            return True
    except OSError:
        return True
    return False


def status() -> dict[str, Any]:
    meta = load_meta() or {}
    enc = encryption_enabled()
    return {
        "enabled": enc,
        "needs_unlock": needs_unlock(),
        "plaintext_present": plaintext_present(),
        "sealed_present": sealed_present(),
        "wrap": meta.get("wrap") or ("password" if meta.get("wrapped_dek") else None),
        "mode_hint": meta.get("mode_hint"),
        "version": meta.get("version"),
        "db_path": str(plaintext_db_path()),
        "sealed_path": str(sealed_db_path()),
        "meta_path": str(crypto_meta_path()),
        "notes": [
            "When encryption is on, every process start requires unlock (no silent plaintext boot).",
            "Without your PIN/password (or device DEK for Hello), sealed books cannot be recovered.",
            "Disabling encryption requires unlock first; clearing lock without secret does not wipe seal.",
        ],
    }


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _derive_kek(secret: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    AESGCM, PBKDF2HMAC, hashes = _crypto()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=DEK_LEN,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(secret.encode("utf-8"))


def _aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    AESGCM, _, _ = _crypto()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def _aes_decrypt(key: bytes, blob: bytes) -> bytes:
    AESGCM, _, _ = _crypto()
    if len(blob) < 13:
        raise ValueError("Corrupt ciphertext")
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ct, None)


def wrap_dek(dek: bytes, secret: str) -> dict[str, str]:
    salt = os.urandom(16)
    kek = _derive_kek(secret, salt)
    wrapped = _aes_encrypt(kek, dek)
    return {
        "kdf": "pbkdf2-sha256",
        "iterations": str(KDF_ITERATIONS),
        "salt": _b64e(salt),
        "wrapped_dek": _b64e(WRAP_MAGIC + wrapped),
    }


def unwrap_dek(secret: str, meta: dict[str, Any]) -> bytes:
    salt = _b64d(meta["salt"])
    iterations = int(meta.get("iterations") or KDF_ITERATIONS)
    kek = _derive_kek(secret, salt, iterations)
    raw = _b64d(meta["wrapped_dek"])
    if raw.startswith(WRAP_MAGIC):
        raw = raw[len(WRAP_MAGIC) :]
    return _aes_decrypt(kek, raw)


def seal_bytes(dek: bytes, plaintext: bytes) -> bytes:
    body = _aes_encrypt(dek, plaintext)
    return SEAL_MAGIC + body


def unseal_bytes(dek: bytes, blob: bytes) -> bytes:
    if not blob.startswith(SEAL_MAGIC):
        raise ValueError("Not an HonestSpend sealed database (bad magic)")
    return _aes_decrypt(dek, blob[len(SEAL_MAGIC) :])


def _wipe_or_remove(path: Path) -> None:
    """Best-effort remove; if locked (Windows), poison so SQLite won't open it."""
    import time

    if not path.is_file():
        return
    trash = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.trash")
    for _ in range(12):
        try:
            path.replace(trash)
            break
        except OSError:
            time.sleep(0.05)
    else:
        # Poison in place so plaintext_present() is False even if unlink fails
        try:
            with open(path, "r+b", buffering=0) as f:
                f.seek(0)
                f.write(b"\x00" * 64)
                f.truncate(0)
        except OSError as e:
            log.warning("Could not poison %s: %s", path, e)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    try:
        if trash.is_file():
            with open(trash, "r+b", buffering=0) as f:
                size = max(trash.stat().st_size, 0)
                f.write(os.urandom(min(size, 256 * 1024)) if size else b"")
            trash.unlink(missing_ok=True)
    except Exception as e:
        log.warning("Trash cleanup incomplete for %s: %s", trash, e)
        try:
            trash.unlink(missing_ok=True)
        except Exception:
            pass


def seal_database(dek: bytes) -> dict[str, Any]:
    """Encrypt plaintext DB → sealed; remove/poison plaintext."""
    plain = plaintext_db_path()
    if not plaintext_present():
        if sealed_present():
            return {"ok": True, "already_sealed": True, "path": str(sealed_db_path())}
        raise ValueError("No plaintext database to seal")
    # sqlite may leave WAL; checkpoint is caller's job (dispose engine first)
    raw = plain.read_bytes()
    if not raw.startswith(b"SQLite format 3"):
        raise ValueError("Plaintext path is not a SQLite database")
    sealed = seal_bytes(dek, raw)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    dest = sealed_db_path()
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(sealed)
    tmp.replace(dest)
    for suffix in ("-wal", "-shm", "-journal"):
        _wipe_or_remove(Path(str(plain) + suffix))
    _wipe_or_remove(plain)
    if plaintext_present():
        raise OSError(
            f"Could not clear plaintext database at {plain} — close all connections and retry seal."
        )
    return {
        "ok": True,
        "sealed_path": str(dest),
        "size": len(sealed),
        "sealed_utc": datetime.now(timezone.utc).isoformat(),
    }


def unseal_database(dek: bytes) -> dict[str, Any]:
    """Decrypt sealed → plaintext DB path for the engine."""
    sealed_p = sealed_db_path()
    if not sealed_p.is_file():
        if plaintext_present():
            return {"ok": True, "already_open": True, "path": str(plaintext_db_path())}
        raise ValueError("No sealed database found")
    blob = sealed_p.read_bytes()
    plain = unseal_bytes(dek, blob)
    out = plaintext_db_path()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(plain)
    tmp.replace(out)
    return {
        "ok": True,
        "path": str(out),
        "size": len(plain),
        "unsealed_utc": datetime.now(timezone.utc).isoformat(),
    }


def enable_encryption(
    *,
    secret: str | None = None,
    dek: bytes | None = None,
    mode_hint: str = "pin",
    wrap: str = "password",
    seal_now: bool = False,
) -> dict[str, Any]:
    """Turn on at-rest encryption. Requires existing plaintext books (or empty new DB).

    wrap=password: secret required; DEK wrapped into crypto.json (multi-device with same PIN).
    wrap=client: client holds DEK (Hello); crypto.json has no wrapped_dek.
    """
    existing = load_meta()
    if existing and existing.get("enabled"):
        raise ValueError(
            "Encryption is already enabled. Unlock and use change-secret, or disable encryption first."
        )

    if not plaintext_present():
        # Create empty file so engine can init after — caller should init_db first
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not plaintext_db_path().is_file():
            raise ValueError(
                "No database yet. Complete first engine start, then enable encryption."
            )

    if dek is None:
        dek = secrets.token_bytes(DEK_LEN)
    if len(dek) != DEK_LEN:
        raise ValueError("DEK must be 32 bytes")

    meta: dict[str, Any] = {
        "version": CRYPTO_VERSION,
        "enabled": True,
        "mode_hint": mode_hint,
        "wrap": wrap,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    if wrap == "password":
        if not secret or len(secret) < 4:
            raise ValueError("PIN/password required to wrap the database key")
        meta.update(wrap_dek(dek, secret))
    elif wrap == "client":
        meta["wrap"] = "client"
        # Client must retain DEK; engine unlock accepts dek_b64
    else:
        raise ValueError("wrap must be password or client")

    save_meta(meta)
    out: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "wrap": wrap,
        "mode_hint": mode_hint,
    }
    # Only return raw DEK for client-held wraps (Windows Hello). Password wrap never needs it.
    if wrap == "client":
        out["dek_b64"] = _b64e(dek)
    if seal_now:
        out["seal"] = seal_database(dek)
    return out


def disable_encryption(*, secret: str | None = None, dek: bytes | None = None) -> dict[str, Any]:
    """Remove encryption after ensuring plaintext is present (unlocked)."""
    if not encryption_enabled():
        return {"ok": True, "enabled": False}
    if needs_unlock():
        # try unseal first
        d = dek or (unwrap_dek(secret or "", load_meta() or {}) if secret else None)
        if d is None:
            raise ValueError("Unlock required before disabling encryption")
        unseal_database(d)
    # leave plaintext; remove seal + meta
    try:
        sealed_db_path().unlink(missing_ok=True)
    except Exception:
        pass
    try:
        crypto_meta_path().unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True, "enabled": False, "message": "Encryption disabled; books are plaintext."}


def resolve_dek(
    *,
    secret: str | None = None,
    dek_b64: str | None = None,
) -> bytes:
    if dek_b64:
        dek = _b64d(dek_b64.strip())
        if len(dek) != DEK_LEN:
            raise ValueError("Invalid DEK length")
        return dek
    meta = load_meta()
    if not meta:
        raise ValueError("Encryption is not configured")
    if meta.get("wrap") == "client" and not dek_b64:
        raise ValueError("This vault uses device DEK — pass dek_b64 after Windows Hello")
    if not secret:
        raise ValueError("PIN or password required")
    return unwrap_dek(secret, meta)


def change_secret(
    *,
    old_secret: str | None = None,
    new_secret: str,
    dek_b64: str | None = None,
    mode_hint: str | None = None,
) -> dict[str, Any]:
    """Re-wrap DEK with a new PIN/password (no full re-encrypt)."""
    dek = resolve_dek(secret=old_secret, dek_b64=dek_b64)
    meta = load_meta() or {}
    meta.update(wrap_dek(dek, new_secret))
    meta["wrap"] = "password"
    meta["mode_hint"] = mode_hint or meta.get("mode_hint") or "pin"
    meta["rotated_utc"] = datetime.now(timezone.utc).isoformat()
    meta["enabled"] = True
    save_meta(meta)
    return {"ok": True, "wrap": "password", "dek_b64": _b64e(dek)}
