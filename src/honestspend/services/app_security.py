"""App lock metadata catalog (secrets live on the client device).

Engine stores only non-secret mode labels for setup checklist / status.
PIN, password hashes, and biometrics stay in the WinUI / native client.
"""

from __future__ import annotations

from typing import Any

# Client unlock modes
LOCK_MODES = (
    ("none", "No lock", "Open instantly on this device."),
    ("pin", "PIN", "4–8 digit code you set here."),
    ("password", "Password", "Password you set here (not your bank login)."),
    ("platform", "Device unlock", "Windows Hello, Face ID, fingerprint — depends on OS."),
)

# Platform capability labels (for multi-client shells; Windows MVP uses windows_hello)
PLATFORM_CAPABILITIES = (
    ("windows_hello", "Windows Hello", "win", True),
    ("windows_pin", "Windows Hello / PIN", "win", True),
    ("ios_face_id", "Face ID", "ios", False),
    ("ios_touch_id", "Touch ID", "ios", False),
    ("android_biometric", "Fingerprint / face unlock", "android", False),
    ("macos_touch_id", "Touch ID", "macos", False),
)

VALID_MODES = frozenset(m[0] for m in LOCK_MODES)
VALID_CAPABILITIES = frozenset(c[0] for c in PLATFORM_CAPABILITIES)


def security_catalog(*, platform: str = "win") -> dict[str, Any]:
    """Modes + capabilities for setup UI (stubs for non-Windows clients)."""
    plat = (platform or "win").strip().lower()
    caps = []
    for cid, label, family, shipped in PLATFORM_CAPABILITIES:
        caps.append(
            {
                "id": cid,
                "label": label,
                "family": family,
                "available_on_this_build": bool(shipped and family == plat),
                "shipped": shipped,
            }
        )
    return {
        "modes": [
            {"id": mid, "label": label, "blurb": blurb} for mid, label, blurb in LOCK_MODES
        ],
        "capabilities": caps,
        "notes": [
            "App lock gates the UI on this device.",
            "PIN/password/Hello also enable AES-256 at-rest sealing of books when encryption is on.",
            "Forget the secret → sealed books cannot be recovered.",
            "Windows Hello DEK is device-local; PIN can open the same sealed file on another PC.",
            "Lock secrets never leave this device and are not in SQLite backups.",
        ],
    }


def normalize_lock_mode(mode: str | None) -> str:
    m = (mode or "none").strip().lower()
    if m not in VALID_MODES:
        raise ValueError(f"Unknown lock mode: {mode}. Use none, pin, password, or platform.")
    return m


def normalize_capability(cap: str | None) -> str | None:
    if not cap:
        return None
    c = cap.strip().lower()
    if c not in VALID_CAPABILITIES:
        raise ValueError(f"Unknown platform capability: {cap}")
    return c


def validate_security_payload(
    *,
    mode: str,
    platform_capability: str | None = None,
) -> dict[str, Any]:
    mode_n = normalize_lock_mode(mode)
    cap = normalize_capability(platform_capability)
    if mode_n == "platform" and not cap:
        # Default capability for Windows clients
        cap = "windows_hello"
    if mode_n != "platform":
        cap = None
    return {
        "mode": mode_n,
        "platform_capability": cap,
        "configured": mode_n != "none",
    }
