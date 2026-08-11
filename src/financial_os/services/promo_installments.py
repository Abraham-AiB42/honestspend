"""Promo / installment plan lines (ISB-class) for statement payment carve-outs.

Open lines reduce the statement-pay-in-full amount by principal_remaining and
add monthly_payment into next payment due.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import PromoInstallmentLine

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def _line_open(line: PromoInstallmentLine, as_of: date) -> bool:
    """True when line is active and within its date window on as_of."""
    if not line.active:
        return False
    if line.start_date and line.start_date > as_of:
        return False
    if line.end_date is not None and line.end_date < as_of:
        return False
    return True


def list_promo_lines(
    session: Session,
    account_id: int,
    *,
    active_only: bool = False,
) -> list[PromoInstallmentLine]:
    q = session.query(PromoInstallmentLine).filter(
        PromoInstallmentLine.account_id == account_id
    )
    if active_only:
        q = q.filter(PromoInstallmentLine.active.is_(True))
    return q.order_by(PromoInstallmentLine.id).all()


def open_promo_totals(
    session: Session,
    account_id: int,
    *,
    as_of: date,
) -> tuple[Decimal, Decimal]:
    """Sum open promo lines: (principal_remaining, monthly_due)."""
    lines = list_promo_lines(session, account_id, active_only=True)
    principal = ZERO
    monthly = ZERO
    for line in lines:
        if not _line_open(line, as_of):
            continue
        principal += _d(line.principal_remaining)
        monthly += _d(line.monthly_payment)
    return _q(principal), _q(monthly)


def apply_month_roll(
    session: Session,
    account_id: int,
    *,
    as_of: date,
) -> None:
    """Apply one monthly installment against each open line on the account.

    principal_remaining = max(0, principal - monthly); deactivate when zero.
    """
    lines = list_promo_lines(session, account_id, active_only=True)
    for line in lines:
        if not _line_open(line, as_of):
            continue
        principal = _d(line.principal_remaining)
        monthly = _d(line.monthly_payment)
        # Pay min(principal, monthly) so we never go negative
        new_principal = max(ZERO, principal - monthly)
        line.principal_remaining = _q(new_principal)
        if line.principal_remaining <= ZERO:
            line.principal_remaining = ZERO
            line.active = False
    session.flush()


def roll_line(
    session: Session,
    line_id: int,
    *,
    as_of: date | None = None,
) -> PromoInstallmentLine:
    """Roll a single promo line (same math as apply_month_roll for one row)."""
    as_of = as_of or date.today()
    line = session.get(PromoInstallmentLine, line_id)
    if not line:
        raise ValueError(f"Promo line {line_id} not found")
    if not line.active:
        return line
    principal = _d(line.principal_remaining)
    monthly = _d(line.monthly_payment)
    new_principal = max(ZERO, principal - monthly)
    line.principal_remaining = _q(new_principal)
    if line.principal_remaining <= ZERO:
        line.principal_remaining = ZERO
        line.active = False
    session.flush()
    return line


def create_promo_line(
    session: Session,
    account_id: int,
    *,
    name: str,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    start_date: date,
    end_date: date | None = None,
    source: str = "user",
    active: bool = True,
) -> PromoInstallmentLine:
    """Insert a promo installment line on a credit account."""
    row = PromoInstallmentLine(
        account_id=account_id,
        name=(name or "").strip() or "Promo plan",
        principal_remaining=_q(_d(principal_remaining)),
        monthly_payment=_q(_d(monthly_payment)),
        start_date=start_date,
        end_date=end_date,
        active=bool(active),
        source=(source or "user").strip() or "user",
    )
    session.add(row)
    session.flush()
    return row


def line_to_dict(line: PromoInstallmentLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "account_id": line.account_id,
        "name": line.name,
        "principal_remaining": str(_q(_d(line.principal_remaining))),
        "monthly_payment": str(_q(_d(line.monthly_payment))),
        "start_date": line.start_date.isoformat() if line.start_date else None,
        "end_date": line.end_date.isoformat() if line.end_date else None,
        "active": bool(line.active),
        "source": line.source or "user",
    }
