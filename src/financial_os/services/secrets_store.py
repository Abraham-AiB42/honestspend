"""Local-only secrets for BYOK Plaid + AI providers.

Never uploaded. Backups intentionally omit this file (only the SQLite DB is zipped).
On Windows, secret values are DPAPI-protected when possible; other OS: ACL-aware file only.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from financial_os.config import settings

log = logging.getLogger("honestspend.secrets")

SECRETS_VERSION = 1
PLAID_ITEM_TRIAL_LIMIT = 10

# Providers we surface in setup (extensible)
AI_PROVIDERS = (
    ("xai", "Grok (xAI)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("custom", "Other / custom"),
)


def secrets_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "secrets.json"


def _dpapi_protect(raw: bytes) -> str:
    """Return base64 payload; prefix win-dpapi: or plain:"""
    if sys.platform != "win32":
        return "plain:" + base64.b64encode(raw).decode("ascii")
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        in_blob = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "HonestSpend",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise OSError("CryptProtectData failed")
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
        return "win-dpapi:" + base64.b64encode(encrypted).decode("ascii")
    except Exception as e:
        log.warning("DPAPI protect failed, storing plain local: %s", e)
        return "plain:" + base64.b64encode(raw).decode("ascii")


def _dpapi_unprotect(token: str) -> bytes:
    if token.startswith("plain:"):
        return base64.b64decode(token[6:])
    if not token.startswith("win-dpapi:"):
        # legacy raw string
        return token.encode("utf-8")
    if sys.platform != "win32":
        raise ValueError("DPAPI secret on non-Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    raw = base64.b64decode(token[10:])
    in_blob = DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _enc_str(value: str | None) -> str | None:
    if not value:
        return None
    return _dpapi_protect(value.encode("utf-8"))


def _dec_str(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _dpapi_unprotect(token).decode("utf-8")
    except Exception as e:
        log.warning("Failed to decrypt secret field: %s", e)
        return None


def _mask(value: str | None, keep: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * (len(value) - keep) + value[-keep:]


def load_raw() -> dict[str, Any]:
    path = secrets_path()
    if not path.is_file():
        return {"version": SECRETS_VERSION, "plaid": {}, "ai": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": SECRETS_VERSION, "plaid": {}, "ai": {}}
        data.setdefault("version", SECRETS_VERSION)
        data.setdefault("plaid", {})
        data.setdefault("ai", {})
        return data
    except Exception as e:
        log.warning("Could not read secrets.json: %s", e)
        return {"version": SECRETS_VERSION, "plaid": {}, "ai": {}}


def _write_raw(data: dict[str, Any]) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    # atomic write
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    try:
        if sys.platform != "win32":
            os.chmod(path, 0o600)
    except Exception:
        pass


def get_plaid_credentials() -> dict[str, str | None]:
    raw = load_raw().get("plaid") or {}
    return {
        "client_id": _dec_str(raw.get("client_id")) if raw.get("client_id") else None,
        "secret": _dec_str(raw.get("secret")) if raw.get("secret") else None,
        "env": raw.get("env") or "sandbox",
    }


def save_plaid_credentials(
    *,
    client_id: str,
    secret: str,
    env: str = "sandbox",
) -> dict[str, Any]:
    client_id = (client_id or "").strip()
    secret = (secret or "").strip()
    env = (env or "sandbox").strip().lower()
    if env not in ("sandbox", "development", "production"):
        raise ValueError("env must be sandbox, development, or production")
    if not client_id or not secret:
        raise ValueError("client_id and secret are required")
    data = load_raw()
    data["plaid"] = {
        "client_id": _enc_str(client_id),
        "secret": _enc_str(secret),
        "env": env,
    }
    _write_raw(data)
    apply_secrets_to_settings()
    return plaid_credentials_status()


def clear_plaid_credentials() -> dict[str, Any]:
    data = load_raw()
    data["plaid"] = {}
    _write_raw(data)
    # Do not clear env vars if set; only clear process overrides from file
    apply_secrets_to_settings(clear_plaid_if_empty=True)
    return plaid_credentials_status()


def plaid_credentials_status() -> dict[str, Any]:
    # Prefer live settings (env + file applied)
    cid = settings.plaid_client_id
    sec = settings.plaid_secret
    env = settings.plaid_env or "sandbox"
    file_has = bool((load_raw().get("plaid") or {}).get("client_id"))
    return {
        "configured": bool(cid and sec),
        "enabled": settings.plaid_enabled,
        "env": env,
        "client_id_masked": _mask(cid, 6) if cid else None,
        "has_secret": bool(sec),
        "source": "local_file" if file_has else ("env" if cid else None),
        "path": str(secrets_path()),
        "item_limit": PLAID_ITEM_TRIAL_LIMIT,
        "signup_url": "https://dashboard.plaid.com/signup",
        "keys_url": "https://dashboard.plaid.com/developers/keys",
    }


def get_ai_providers() -> dict[str, Any]:
    raw_ai = load_raw().get("ai") or {}
    providers = []
    for pid, label in AI_PROVIDERS:
        entry = raw_ai.get(pid) or {}
        key = _dec_str(entry.get("api_key")) if entry.get("api_key") else None
        # xai also from env
        if pid == "xai" and not key:
            key = settings.xai_api_key
        providers.append(
            {
                "id": pid,
                "label": label,
                "configured": bool(key),
                "api_key_masked": _mask(key) if key else None,
                "base_url": entry.get("base_url"),
            }
        )
    return {
        "providers": providers,
        "path": str(secrets_path()),
        "grok_enabled": settings.grok_enabled,
    }


def save_ai_credentials(
    *,
    provider: str,
    api_key: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    if provider not in {p[0] for p in AI_PROVIDERS}:
        raise ValueError(f"Unknown provider: {provider}")
    if not api_key:
        raise ValueError("api_key is required")
    data = load_raw()
    ai = data.setdefault("ai", {})
    ai[provider] = {
        "api_key": _enc_str(api_key),
    }
    if base_url:
        ai[provider]["base_url"] = base_url.strip()
    _write_raw(data)
    apply_secrets_to_settings()
    return get_ai_providers()


def clear_ai_credentials(provider: str) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    data = load_raw()
    ai = data.setdefault("ai", {})
    ai.pop(provider, None)
    _write_raw(data)
    apply_secrets_to_settings(clear_ai_provider=provider)
    return get_ai_providers()


def apply_secrets_to_settings(
    *,
    clear_plaid_if_empty: bool = False,
    clear_ai_provider: str | None = None,
) -> None:
    """Hot-reload process settings from secrets file (env still wins if set and file empty)."""
    plaid = get_plaid_credentials()
    if plaid.get("client_id") and plaid.get("secret"):
        settings.plaid_client_id = plaid["client_id"]
        settings.plaid_secret = plaid["secret"]
        if plaid.get("env"):
            settings.plaid_env = str(plaid["env"])
    elif clear_plaid_if_empty:
        # Only clear if not from environment
        env_id = os.environ.get("FOS_PLAID_CLIENT_ID")
        if not env_id:
            settings.plaid_client_id = None
            settings.plaid_secret = None

    raw_ai = load_raw().get("ai") or {}
    xai_entry = raw_ai.get("xai") or {}
    xai_key = _dec_str(xai_entry.get("api_key")) if xai_entry.get("api_key") else None
    if xai_key:
        settings.xai_api_key = xai_key
    elif clear_ai_provider == "xai" and not os.environ.get("FOS_XAI_API_KEY"):
        settings.xai_api_key = None

    log.debug(
        "Secrets applied: plaid=%s grok=%s",
        settings.plaid_enabled,
        settings.grok_enabled,
    )
