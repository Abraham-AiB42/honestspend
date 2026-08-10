"""Commercial license state (local-first; optional remote verify later).

See docs/LICENSING.md. Default is OSS-friendly: enforce off → always usable.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from financial_os.config import settings

SCHEMA = 1
PLAN_LIFETIME = "lifetime_personal"
PRICE_USD = 49.99
DEFAULT_MAX_DEVICES = 5
DEFAULT_GRACE_DAYS = 90

# Local / CI test keys — never ship as the only production unlock path.
DEV_KEYS = frozenset(
    {
        "HS-DEV-LIFETIME",
        "HS-TEST-LIFETIME",
    }
)

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_]{7,128}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        raw = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def license_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "license.json"


def device_id() -> str:
    """Stable-ish device fingerprint (not a secret; not hardware TPM)."""
    parts = [
        platform.node() or "node",
        platform.system() or "os",
        platform.machine() or "arch",
        str(settings.data_dir.resolve()),
    ]
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"dev_{h}"


def key_fingerprint(key: str) -> str:
    norm = (key or "").strip().upper()
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


def enforce_enabled() -> bool:
    return bool(getattr(settings, "license_enforce", False))


def distribution() -> str:
    """How this engine instance was launched: store | unpackaged | unknown."""
    d = str(getattr(settings, "license_distribution", "") or "").strip().lower()
    if d in ("store", "unpackaged", "sideload", "dev"):
        return d
    return "unknown"


def grace_days() -> int:
    try:
        d = int(getattr(settings, "license_grace_days", DEFAULT_GRACE_DAYS) or DEFAULT_GRACE_DAYS)
    except (TypeError, ValueError):
        d = DEFAULT_GRACE_DAYS
    return max(1, min(d, 3650))


def allow_dev_keys() -> bool:
    if not enforce_enabled():
        return True
    return bool(getattr(settings, "license_allow_dev_keys", False))


def license_server_url() -> str | None:
    u = getattr(settings, "license_server_url", None)
    if not u:
        return None
    s = str(u).strip().rstrip("/")
    return s or None


def _load_raw() -> dict[str, Any] | None:
    path = license_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_raw(data: dict[str, Any]) -> None:
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_license() -> dict[str, Any]:
    path = license_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    return get_status()


@dataclass
class LicenseEval:
    licensed: bool
    reason: str
    within_grace: bool
    record: dict[str, Any] | None


def _eval_record(rec: dict[str, Any] | None) -> LicenseEval:
    if not rec:
        return LicenseEval(False, "no_license", False, None)

    expires = _parse_iso(rec.get("expires_at") if isinstance(rec.get("expires_at"), str) else None)
    now = _utc_now()
    if expires is not None and now > expires:
        return LicenseEval(False, "expired", False, rec)

    last = _parse_iso(rec.get("last_verified_at") if isinstance(rec.get("last_verified_at"), str) else None)
    if last is None:
        last = _parse_iso(rec.get("activated_at") if isinstance(rec.get("activated_at"), str) else None)
    if last is None:
        return LicenseEval(False, "missing_timestamps", False, rec)

    grace_end = last + timedelta(days=grace_days())
    within = now <= grace_end
    if not within:
        # Local lifetime keys still OK offline past grace if no server required
        source = str(rec.get("source") or "")
        if source in ("key", "dev", "direct") and expires is None:
            return LicenseEval(True, "lifetime_offline", False, rec)
        return LicenseEval(False, "grace_expired", False, rec)

    return LicenseEval(True, "ok", True, rec)


def get_status() -> dict[str, Any]:
    """Public status for UI / store gate."""
    rec = _load_raw()
    ev = _eval_record(rec)
    enforce = enforce_enabled()

    # OSS / freeware default: product usable without purchase.
    if not enforce:
        product_licensed = True
        mode = "oss_unlocked"
        gate = "open"
    else:
        product_licensed = ev.licensed
        mode = "commercial"
        gate = "ok" if product_licensed else "needs_activation"

    out: dict[str, Any] = {
        "product": "HonestSpend",
        "price_usd": PRICE_USD,
        "plan_default": PLAN_LIFETIME,
        "mode": mode,
        "enforce": enforce,
        "licensed": product_licensed,
        "gate": gate,
        "distribution": distribution(),
        "has_local_record": rec is not None,
        "record_valid": ev.licensed,
        "reason": ev.reason if rec else ("oss_default" if not enforce else "no_license"),
        "within_grace": ev.within_grace,
        "grace_days": grace_days(),
        "device_id": device_id(),
        "server_configured": license_server_url() is not None,
        "server_url": license_server_url(),
        "max_devices": DEFAULT_MAX_DEVICES,
        "privacy_url": "https://honestspend.net/privacy/",
        "site_url": "https://honestspend.net/",
        "store_url": "https://apps.microsoft.com/search?query=HonestSpend",
        "buy_hint": (
            "One-time purchase ($49.99 list) on Microsoft Store. "
            "Same license unlocks official clients as they ship."
        ),
        "activate_hint": (
            "If you bought on Microsoft Store, use Restore Store purchase. "
            "Or enter a license key from your receipt / account email."
        ),
        "promo_hint": "Pre-launch 50% off through October 21, 2026 where the Store promo is active.",
    }

    if rec:
        out["license_id"] = rec.get("license_id")
        out["plan"] = rec.get("plan") or PLAN_LIFETIME
        out["email"] = rec.get("email")
        out["source"] = rec.get("source")
        out["store_kind"] = rec.get("store_kind")
        out["is_trial"] = bool(rec.get("is_trial"))
        out["activated_at"] = rec.get("activated_at")
        out["last_verified_at"] = rec.get("last_verified_at")
        out["expires_at"] = rec.get("expires_at")
        out["key_fingerprint"] = rec.get("key_fingerprint")
        # Never echo full key
        fp = str(rec.get("key_fingerprint") or "")
        out["key_hint"] = (fp[-8:] if len(fp) >= 8 else None)

    return out


def activate_key(key: str, email: str | None = None) -> dict[str, Any]:
    """Activate with a license key (local + optional future server)."""
    raw = (key or "").strip()
    if not raw or not KEY_RE.match(raw):
        raise ValueError("Invalid license key format")

    norm = raw.upper()
    is_dev = norm in DEV_KEYS
    if is_dev and not allow_dev_keys():
        raise ValueError("Developer license keys are disabled in this build")

    email_clean = (email or "").strip() or None
    if email_clean and ("@" not in email_clean or len(email_clean) > 254):
        raise ValueError("Invalid email")

    now = _utc_now()
    server = license_server_url()
    source = "dev" if is_dev else "key"
    remote_note = None

    # Future: POST to license server. For now local-only accept.
    if server and not is_dev:
        remote_note = "server_configured_but_local_activate_stub"
        # Keep local activate so UI works before Worker ships.

    rec = {
        "schema": SCHEMA,
        "license_id": f"lic_{uuid.uuid4().hex[:16]}",
        "key_fingerprint": key_fingerprint(norm),
        "email": email_clean,
        "plan": PLAN_LIFETIME,
        "max_devices": DEFAULT_MAX_DEVICES,
        "source": source,
        "activated_at": _iso(now),
        "last_verified_at": _iso(now),
        "expires_at": None,
        "device_id": device_id(),
        "token": None,
        "notes": remote_note,
    }
    _save_raw(rec)
    status = get_status()
    status["activated"] = True
    status["message"] = (
        "Developer lifetime license activated (local)."
        if is_dev
        else "License activated on this device. Use the same key on other official clients."
    )
    return status


def activate_store(
    *,
    is_active: bool,
    store_kind: str = "ms_store",
    is_trial: bool = False,
    store_sku: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Record Microsoft Store (or other store) entitlement from the client.

    The WinUI client verifies StoreContext.GetAppLicenseAsync() and posts the result.
    """
    kind = (store_kind or "ms_store").strip().lower() or "ms_store"
    if kind not in ("ms_store", "apple", "google", "direct"):
        kind = "ms_store"

    if not is_active:
        # Do not wipe a valid key-based license if Store says inactive (e.g. offline Store)
        rec = _load_raw()
        if rec and str(rec.get("source") or "") in ("key", "dev", "direct"):
            status = get_status()
            status["store_checked"] = True
            status["store_active"] = False
            status["message"] = "Store purchase not active; existing key license kept."
            return status
        # Clear only store-sourced licenses
        if rec and str(rec.get("source") or "") in ("ms_store", "apple", "google"):
            clear_license()
        status = get_status()
        status["store_checked"] = True
        status["store_active"] = False
        status["message"] = detail or "No active Microsoft Store license on this account."
        return status

    now = _utc_now()
    existing = _load_raw() or {}
    rec = {
        "schema": SCHEMA,
        "license_id": existing.get("license_id") or f"lic_{uuid.uuid4().hex[:16]}",
        "key_fingerprint": existing.get("key_fingerprint"),
        "email": existing.get("email"),
        "plan": PLAN_LIFETIME if not is_trial else "trial",
        "max_devices": DEFAULT_MAX_DEVICES,
        "source": kind,
        "store_kind": kind,
        "store_sku": (store_sku or "").strip() or None,
        "is_trial": bool(is_trial),
        "activated_at": existing.get("activated_at") or _iso(now),
        "last_verified_at": _iso(now),
        "expires_at": None,
        "device_id": device_id(),
        "token": existing.get("token"),
        "notes": detail,
    }
    _save_raw(rec)
    status = get_status()
    status["activated"] = True
    status["store_checked"] = True
    status["store_active"] = True
    status["message"] = (
        "Microsoft Store trial active on this device."
        if is_trial
        else "Microsoft Store purchase verified on this device."
    )
    return status


def refresh_license() -> dict[str, Any]:
    """Bump last_verified when local record is still valid; stub remote verify."""
    rec = _load_raw()
    if not rec:
        raise ValueError("No local license to refresh")

    ev = _eval_record(rec)
    if not ev.licensed and enforce_enabled():
        raise ValueError(f"License not valid ({ev.reason})")

    server = license_server_url()
    now = _utc_now()
    rec = dict(rec)
    rec["last_verified_at"] = _iso(now)
    if server:
        rec["notes"] = "refresh_local_stub_server_pending"
    _save_raw(rec)
    status = get_status()
    status["refreshed"] = True
    status["message"] = "License verification timestamp updated."
    return status
