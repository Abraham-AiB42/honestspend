"""Permission stack foundation (local multi-role access).

Roles (PRODUCT.md):
  owner       — full control
  bookkeeper  — categorize, reconcile, intermix; no settings/delete destructive
  cpa_viewer  — read books + tax packet export only
  viewer      — read-only Spendable / dashboards

Auth: optional X-API-Key header maps to AppUser.api_token.
If no API key and auth not required, defaults to local owner (desktop).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session


class Role(str, Enum):
    owner = "owner"
    bookkeeper = "bookkeeper"
    cpa_viewer = "cpa_viewer"
    viewer = "viewer"


# Capability flags
CAPS = {
    Role.owner: {
        "read",
        "write_txns",
        "write_accounts",
        "write_settings",
        "categorize",
        "intermix",
        "tax_export",
        "connect_banks",
        "manage_users",
        "delete",
    },
    Role.bookkeeper: {
        "read",
        "write_txns",
        "categorize",
        "intermix",
        "tax_export",
    },
    Role.cpa_viewer: {
        "read",
        "tax_export",
    },
    Role.viewer: {
        "read",
    },
}

# Map HTTP path prefixes → required capability (write methods)
WRITE_CAPS = {
    "/api/settings": "write_settings",
    "/api/accounts": "write_accounts",
    "/api/transactions": "write_txns",
    "/api/scheduled": "write_txns",
    "/api/payroll-packages": "write_txns",
    "/api/intermix": "intermix",
    "/api/categorize": "categorize",
    "/api/rules": "categorize",
    "/api/import": "write_txns",
    "/api/plaid": "connect_banks",
    "/api/setup": "write_settings",
    "/api/crypto": "write_settings",
    "/api/ai": "write_settings",
    "/api/permissions/users": "manage_users",
    "/api/tax": "tax_export",
    "/api/profiles": "write_settings",
    "/api/onboarding": "write_settings",
    "/api/debt": "write_settings",
    "/api/backup": "write_settings",
    "/api/credit/profile": "write_settings",
    "/api/liquidity": "read",  # rescue is advisory; still requires auth identity
    "/api/fees": "write_txns",
    "/api/transfers": "write_txns",
    "/api/ifpp/simulate": "read",
    "/api/reconcile": "write_txns",
    "/api/promo-clock": "write_txns",
    "/api/tax-vault": "write_settings",
    "/api/autopay": "write_txns",
    "/api/payments": "write_txns",
}


@dataclass
class AccessContext:
    user_id: str = "local"
    role: Role = Role.owner
    display_name: str = "Owner"
    username: str = "owner"
    authenticated: bool = False

    def can(self, capability: str) -> bool:
        return capability in CAPS.get(self.role, set())

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise PermissionError(f"Role {self.role.value} cannot {capability}")


def list_roles() -> list[dict]:
    return [
        {
            "role": r.value,
            "capabilities": sorted(CAPS[r]),
            "description": {
                Role.owner: "Full control — local default",
                Role.bookkeeper: "Day-to-day books, no destructive settings",
                Role.cpa_viewer: "Read + tax packet only",
                Role.viewer: "Dashboards read-only",
            }[r],
        }
        for r in Role
    ]


def default_context() -> AccessContext:
    return AccessContext()


def generate_api_token() -> str:
    return f"lr_{secrets.token_urlsafe(24)}"


def resolve_context(session: Session, api_token: str | None) -> AccessContext:
    from financial_os.db import AppUser

    if not api_token:
        # Desktop / local open access as owner
        user = session.query(AppUser).filter(AppUser.username == "owner").first()
        if user:
            return AccessContext(
                user_id=str(user.id),
                role=Role(user.role) if user.role in {r.value for r in Role} else Role.owner,
                display_name=user.display_name,
                username=user.username,
                authenticated=False,
            )
        return default_context()

    user = (
        session.query(AppUser)
        .filter(AppUser.api_token == api_token, AppUser.active.is_(True))
        .first()
    )
    if not user:
        raise PermissionError("Invalid API token")
    role = Role(user.role) if user.role in {r.value for r in Role} else Role.viewer
    return AccessContext(
        user_id=str(user.id),
        role=role,
        display_name=user.display_name,
        username=user.username,
        authenticated=True,
    )


def capability_for_request(method: str, path: str) -> str | None:
    """Return required capability for mutating requests; None if read-only open."""
    # User admin + audit: owner only
    if path.startswith("/api/permissions/users") or path.startswith("/api/permissions/audit"):
        return "manage_users"
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return "read"
    # Special-case simulate (POST but read-level)
    if path.startswith("/api/ifpp/simulate") or path.startswith("/api/liquidity"):
        return "read"
    for prefix, cap in WRITE_CAPS.items():
        if path.startswith(prefix):
            return cap
    # default write needs write_txns for unknown POST
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        return "write_txns"
    return "read"


def active_user_count(session: Session) -> int:
    from financial_os.db import AppUser

    return session.query(AppUser).filter(AppUser.active.is_(True)).count()


def multi_user_mode(session: Session) -> bool:
    """When more than one active user exists, API keys are mandatory (even on loopback)."""
    return active_user_count(session) > 1


def auth_status(session: Session) -> dict:
    n = active_user_count(session)
    return {
        "active_users": n,
        "multi_user_mode": n > 1,
        "api_key_required": n > 1,
        "hint": (
            "Second active user enables multi-user mode: all API calls need X-API-Key."
            if n > 1
            else "Single-user local mode: loopback may omit X-API-Key (owner)."
        ),
    }
