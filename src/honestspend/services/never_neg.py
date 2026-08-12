"""Never-negative checking enforcement on write paths."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, AppSettings

ZERO = Decimal("0")


class WouldGoNegative(Exception):
    """Raised when a write would push checking below zero."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("message", "would_go_negative"))
        self.payload = payload


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def enforcement_mode(session: Session) -> str:
    s = session.get(AppSettings, 1) or AppSettings(id=1)
    mode = (getattr(s, "never_negative_enforcement", None) or "warn").lower()
    if mode not in ("off", "warn", "hard"):
        return "warn"
    return mode


def projected_cash_balance(account: Account, amount: Decimal) -> Decimal:
    """Signed amount added to cash/savings balance (same convention as create_transaction)."""
    return _d(account.current_balance) + _d(amount)


def check_cash_outflow(
    session: Session,
    *,
    account: Account | None,
    amount: Decimal,
    confirm_unsafe: bool = False,
) -> None:
    """Refuse or require confirm when checking would go negative.

    Product rule: raw checking balance must not go below $0 (buffer is IFPP soft floor only).
    """
    mode = enforcement_mode(session)
    if mode == "off" or account is None:
        return
    if (account.kind or "") != "checking":
        return
    # Only care about outflows (negative amount for cash)
    if _d(amount) >= ZERO:
        return

    projected = projected_cash_balance(account, amount)
    if projected >= ZERO:
        return

    payload = {
        "code": "would_go_negative",
        "message": (
            f"This would make checking '{account.nickname}' negative "
            f"(${projected:.2f}). Analyze rescue options or confirm if allowed."
        ),
        "account_id": account.id,
        "account_name": account.nickname,
        "current_balance": str(_d(account.current_balance)),
        "amount": str(_d(amount)),
        "projected_balance": str(projected),
        "enforcement": mode,
        "confirm_required": mode == "warn",
        "rescue_hint": "POST /api/liquidity/rescue with amount and account_id",
    }
    if mode == "hard":
        raise WouldGoNegative(payload)
    # warn
    if not confirm_unsafe:
        raise WouldGoNegative(payload)
