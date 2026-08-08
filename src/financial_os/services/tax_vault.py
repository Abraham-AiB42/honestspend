"""Tax vault — reserved cash that reduces Spendable (PRODUCT.md)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import AppSettings

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def get_tax_vault(session: Session) -> dict[str, Any]:
    s = session.get(AppSettings, 1) or AppSettings(id=1)
    bal = _d(getattr(s, "tax_vault_balance", None) or 0)
    enabled = bool(getattr(s, "tax_vault_enabled", True))
    rate = getattr(s, "tax_vault_income_rate", None)
    return {
        "enabled": enabled,
        "balance": str(bal),
        "income_rate": str(rate) if rate is not None else None,
        "income_rate_pct": f"{float(rate)*100:.1f}%" if rate is not None else None,
        "note": (
            "Tax vault is virtual reserve — money stays in your bank accounts but "
            "is not counted in Spendable. Prevents April surprises."
        ),
    }


def set_tax_vault(
    session: Session,
    *,
    balance: Decimal | None = None,
    enabled: bool | None = None,
    income_rate: Decimal | None = None,
    clear_income_rate: bool = False,
) -> dict[str, Any]:
    s = session.get(AppSettings, 1)
    if not s:
        s = AppSettings(id=1)
        session.add(s)
    if enabled is not None:
        s.tax_vault_enabled = enabled
    if balance is not None:
        s.tax_vault_balance = max(ZERO, _d(balance))
    if clear_income_rate:
        s.tax_vault_income_rate = None
    elif income_rate is not None:
        r = _d(income_rate)
        if r < 0 or r > 1:
            raise ValueError("income_rate must be between 0 and 1")
        s.tax_vault_income_rate = r
    session.flush()
    return get_tax_vault(session)


def adjust_tax_vault(session: Session, delta: Decimal, *, note: str | None = None) -> dict[str, Any]:
    """Add (positive) or release (negative) vault reserve."""
    s = session.get(AppSettings, 1)
    if not s:
        s = AppSettings(id=1)
        session.add(s)
    cur = _d(getattr(s, "tax_vault_balance", None) or 0)
    new = max(ZERO, cur + _d(delta))
    s.tax_vault_balance = new
    s.tax_vault_enabled = True
    session.flush()
    out = get_tax_vault(session)
    out["delta"] = str(_d(delta))
    out["note"] = note or out["note"]
    return out


def suggest_set_aside(gross_income: Decimal, rate: Decimal | None) -> Decimal:
    if rate is None or _d(rate) <= 0:
        return ZERO
    return (abs(_d(gross_income)) * _d(rate)).quantize(Decimal("0.01"))
