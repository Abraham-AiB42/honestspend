"""Password-encrypted backup packages for cloud folder sync.

Format (.lrenc):
  magic "LRE1" (4) + salt (16) + nonce (12) + ciphertext (AES-256-GCM)

Uses cryptography when installed; otherwise returns a clear error.
Destination may be any local folder (OneDrive/Dropbox/iCloud path).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from honestspend.config import settings
from honestspend.services.backup import create_backup, backups_dir


MAGIC = b"LRE1"


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        import os

        return AESGCM, PBKDF2HMAC, hashes, os
    except ImportError as e:
        raise RuntimeError(
            "Encrypted backups require the 'cryptography' package. "
            "Install: pip install cryptography"
        ) from e


def remote_config_path() -> Path:
    return settings.data_dir / "backup_remote.json"


def get_remote_config() -> dict[str, Any]:
    p = remote_config_path()
    if not p.is_file():
        return {
            "destination_folder": None,
            "auto_copy_encrypted": False,
            "hint": "Set a folder (e.g. OneDrive\\HonestSpend\\backups) for encrypted snapshot copies.",
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {
        "destination_folder": data.get("destination_folder"),
        "auto_copy_encrypted": bool(data.get("auto_copy_encrypted")),
        "hint": "Encrypted snapshots only — never put live SQLite on shared cloud for multi-device write.",
    }


def set_remote_config(
    *,
    destination_folder: str | None,
    auto_copy_encrypted: bool = False,
) -> dict[str, Any]:
    cfg = {
        "destination_folder": (destination_folder or "").strip() or None,
        "auto_copy_encrypted": bool(auto_copy_encrypted),
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    remote_config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if cfg["destination_folder"]:
        Path(cfg["destination_folder"]).mkdir(parents=True, exist_ok=True)
    return get_remote_config()


def _derive_key(password: str, salt: bytes, PBKDF2HMAC, hashes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_bytes(plaintext: bytes, password: str) -> bytes:
    AESGCM, PBKDF2HMAC, hashes, os = _crypto()
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt, PBKDF2HMAC, hashes)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return MAGIC + salt + nonce + ct


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    AESGCM, PBKDF2HMAC, hashes, _os = _crypto()
    if not blob.startswith(MAGIC):
        raise ValueError("Not a HonestSpend encrypted backup (bad magic)")
    salt = blob[4:20]
    nonce = blob[20:32]
    ct = blob[32:]
    key = _derive_key(password, salt, PBKDF2HMAC, hashes)
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as e:
        raise ValueError("Decrypt failed — wrong password or corrupt file") from e


def create_encrypted_backup(
    *,
    password: str,
    note: str | None = None,
    copy_to_remote: bool = True,
) -> dict[str, Any]:
    """Create plain zip backup then wrap as .lrenc; optionally copy to remote folder."""
    plain = create_backup(as_zip=True, note=note or "encrypted")
    plain_path = Path(plain["path"])
    raw = plain_path.read_bytes()
    enc = encrypt_bytes(raw, password)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dest = backups_dir() / f"ledger_{stamp}.lrenc"
    n = 0
    while dest.exists():
        n += 1
        dest = backups_dir() / f"ledger_{stamp}_{n}.lrenc"
    dest.write_bytes(enc)

    remote_path = None
    cfg = get_remote_config()
    if copy_to_remote and cfg.get("destination_folder"):
        remote_dir = Path(cfg["destination_folder"])
        remote_dir.mkdir(parents=True, exist_ok=True)
        remote_path = remote_dir / dest.name
        shutil.copy2(dest, remote_path)

    return {
        "ok": True,
        "name": dest.name,
        "path": str(dest),
        "size_bytes": dest.stat().st_size,
        "format": "lrenc",
        "plain_backup": plain["name"],
        "remote_path": str(remote_path) if remote_path else None,
        "hint": "Store the password separately. Restore via decrypt then staged restore.",
    }


def decrypt_file_to_backup(
    *,
    path: Path | str,
    password: str,
) -> dict[str, Any]:
    """Decrypt .lrenc into backups/ as a normal ledger_*.zip for staged restore."""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    plain = decrypt_bytes(src.read_bytes(), password)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    out = backups_dir() / f"ledger_{stamp}_decrypted.zip"
    out.write_bytes(plain)
    return {
        "ok": True,
        "name": out.name,
        "path": str(out),
        "size_bytes": out.stat().st_size,
        "hint": f"Decrypted. POST /api/backup/restore/{out.name} then restart engine.",
    }


def list_encrypted() -> list[dict[str, Any]]:
    items = []
    for p in sorted(backups_dir().glob("ledger_*.lrenc"), reverse=True):
        st = p.stat()
        items.append(
            {
                "name": p.name,
                "path": str(p),
                "size_bytes": st.st_size,
                "created": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items
