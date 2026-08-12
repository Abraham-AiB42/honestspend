"""Promo / installment plan lines (ISB-class) for statement payment carve-outs.

Open lines reduce the statement-pay-in-full amount by principal_remaining and
add monthly_payment into next payment due.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_UP
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, PromoInstallmentLine

ZERO = Decimal("0")
CENT = Decimal("0.01")
# Safety ceiling so a $1/mo plan cannot produce an unbounded calendar.
# Dynamic length is min(ceil(principal/monthly), this cap) — 24–60 mo plans fit easily.
MAX_PROMO_CALENDAR_MONTHS = 120


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
    """Sum principal_remaining, monthly_due for lines with status=='open' and _line_open(...).

    Pending/declined/inactive never enter next payment.
    monthly_due per line is min(principal_remaining, monthly_payment) so a
    partial final installment is not overstated.
    NULL status is treated as open (legacy rows).
    """
    lines = list_promo_lines(session, account_id, active_only=True)
    principal = ZERO
    monthly = ZERO
    for line in lines:
        st = getattr(line, "status", None)
        if st is not None and st != "open":
            continue
        if not _line_open(line, as_of):
            continue
        rem = _d(line.principal_remaining)
        pay = _d(line.monthly_payment)
        principal += rem
        monthly += min(rem, pay)
    return _q(principal), _q(monthly)


def _account_has_promo_lines(session: Session, account_id: int) -> bool:
    """True when installment lines exist (active or settled) — lines own the balloon."""
    n = (
        session.query(PromoInstallmentLine.id)
        .filter(PromoInstallmentLine.account_id == account_id)
        .limit(1)
        .count()
    )
    return n > 0


def effective_promo_balance(
    session: Session,
    account: Account | int,
    *,
    as_of: date | None = None,
) -> Decimal | None:
    """Promo balloon used by IFPP float / pay policies.

    Open installment lines are the source of truth when any principal remains.
    When lines exist but open principal is 0, return 0 (do not keep a stale
    synced promo_balance). Without lines, fall back to Account.promo_balance.
    """
    as_of = as_of or date.today()
    if isinstance(account, int):
        acct = session.get(Account, account)
        if not acct:
            return None
        account_id = account
        legacy = _d(acct.promo_balance) if acct.promo_balance is not None else None
    else:
        account_id = int(account.id)
        legacy = _d(account.promo_balance) if account.promo_balance is not None else None

    principal, _monthly = open_promo_totals(session, account_id, as_of=as_of)
    if principal > ZERO:
        return principal
    if _account_has_promo_lines(session, account_id):
        return ZERO
    return legacy


def sync_account_promo_balance_from_lines(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
) -> Decimal | None:
    """Keep Account.promo_balance aligned with open installment lines.

    When lines exist with principal, write that total. When lines exist but
    open principal is 0 (paid off), clear promo_balance so IFPP float recovers.
    When the account has never had lines, leave manual promo_balance alone.
    """
    as_of = as_of or date.today()
    acct = session.get(Account, account_id)
    if not acct or (acct.kind or "") != "credit":
        return None
    principal, _ = open_promo_totals(session, account_id, as_of=as_of)
    if principal > ZERO:
        acct.promo_balance = principal
        session.flush()
        return principal
    if _account_has_promo_lines(session, account_id):
        acct.promo_balance = ZERO
        session.flush()
        return ZERO
    return _d(acct.promo_balance) if acct.promo_balance is not None else None


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
    sync_account_promo_balance_from_lines(session, account_id, as_of=as_of)


def roll_line(
    session: Session,
    line_id: int,
    *,
    as_of: date | None = None,
) -> PromoInstallmentLine:
    """Roll a single promo line (same math as apply_month_roll for one row).

    Raises ValueError if the line is not found or not open for as_of
    (future start / past end / inactive) so callers can return a clear error.
    """
    as_of = as_of or date.today()
    line = session.get(PromoInstallmentLine, line_id)
    if not line:
        raise ValueError(f"Promo line {line_id} not found")
    if not line.active:
        return line
    if not _line_open(line, as_of):
        raise ValueError(
            f"Promo line {line_id} is not open for {as_of.isoformat()} "
            "(not yet started or past end_date)"
        )
    principal = _d(line.principal_remaining)
    monthly = _d(line.monthly_payment)
    # Pay min(principal, monthly) so we never go negative
    new_principal = max(ZERO, principal - monthly)
    line.principal_remaining = _q(new_principal)
    if line.principal_remaining <= ZERO:
        line.principal_remaining = ZERO
        line.active = False
    session.flush()
    sync_account_promo_balance_from_lines(session, int(line.account_id), as_of=as_of)
    return line


def create_promo_line(
    session: Session,
    account_id: int | None,
    *,
    name: str,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    start_date: date,
    end_date: date | None = None,
    source: str = "user",
    active: bool = True,
    kind: str = "purchase_plan",
    status: str | None = None,
    offer_type: str | None = None,
    fingerprint: str | None = None,
    linked_txn_id: int | None = None,
    apr: Decimal | None = None,
) -> PromoInstallmentLine:
    """Insert a promo installment line (or pending offer).

    status default: 'pending' if kind=='offer' else 'open'.
    account_id may be None for kind=offer + offer_type=new_card.
    """
    kind_s = (kind or "purchase_plan").strip() or "purchase_plan"
    if status is None:
        status = "pending" if kind_s == "offer" else "open"
    row = PromoInstallmentLine(
        account_id=account_id,
        name=(name or "").strip() or "Promo plan",
        principal_remaining=_q(_d(principal_remaining)),
        monthly_payment=_q(_d(monthly_payment)),
        start_date=start_date,
        end_date=end_date,
        active=bool(active),
        source=(source or "user").strip() or "user",
        kind=kind_s,
        status=status,
        offer_type=offer_type,
        fingerprint=fingerprint,
        linked_txn_id=linked_txn_id,
        apr=_d(apr) if apr is not None else None,
    )
    session.add(row)
    session.flush()
    if account_id is not None:
        sync_account_promo_balance_from_lines(session, account_id, as_of=start_date)
    return row


def update_promo_line(
    session: Session,
    line_id: int,
    *,
    principal_remaining: Decimal | None = None,
    monthly_payment: Decimal | None = None,
    name: str | None = None,
    active: bool | None = None,
) -> PromoInstallmentLine:
    """Patch remaining principal and/or monthly payment (and optional name/active).

    Raises ValueError if the line is not found.
    principal_remaining <= 0 deactivates the line.
    """
    line = session.get(PromoInstallmentLine, line_id)
    if not line:
        raise ValueError(f"Promo line {line_id} not found")
    if name is not None:
        line.name = (name or "").strip() or line.name
    if principal_remaining is not None:
        rem = _q(_d(principal_remaining))
        if rem < ZERO:
            raise ValueError("principal_remaining must be >= 0")
        line.principal_remaining = rem
        if rem <= ZERO:
            line.principal_remaining = ZERO
            line.active = False
    if monthly_payment is not None:
        pay = _q(_d(monthly_payment))
        if pay < ZERO:
            raise ValueError("monthly_payment must be >= 0")
        line.monthly_payment = pay
    if active is not None:
        line.active = bool(active)
    session.flush()
    sync_account_promo_balance_from_lines(session, int(line.account_id))
    return line


def line_to_dict(line: PromoInstallmentLine) -> dict[str, Any]:
    apr = getattr(line, "apr", None)
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
        "kind": getattr(line, "kind", None) or "purchase_plan",
        "status": getattr(line, "status", None) or "open",
        "fingerprint": getattr(line, "fingerprint", None),
        "offer_type": getattr(line, "offer_type", None),
        "apr": str(apr) if apr is not None else None,
        "linked_txn_id": getattr(line, "linked_txn_id", None),
    }


def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, n: int) -> date:
    """Advance calendar month by n (day clamped to month length)."""
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def estimated_months_remaining(
    principal: Decimal | Any,
    monthly: Decimal | Any,
    *,
    max_months: int = MAX_PROMO_CALENDAR_MONTHS,
) -> int:
    """How many installment months until principal is cleared (0 if already paid)."""
    rem = _d(principal)
    pay = _d(monthly)
    if rem <= ZERO:
        return 0
    if pay <= ZERO:
        return 0
    # ceil(rem / pay) but never above max_months
    n = int((rem / pay).to_integral_value(rounding=ROUND_UP))
    return max(1, min(int(max_months), n))


def project_line_calendar(
    line: PromoInstallmentLine,
    *,
    as_of: date | None = None,
    max_months: int = MAX_PROMO_CALENDAR_MONTHS,
) -> dict[str, Any]:
    """Month-by-month amortization for one promo line until paid off or cap.

    Length is **dynamic**: ceil(remaining / monthly), not fixed at 12.
    Supports multi-year financing (e.g. 24-month Home Depot plans).
    Stops early if end_date is set and passed.
    """
    as_of = as_of or date.today()
    rem = _q(_d(line.principal_remaining))
    pay = _q(_d(line.monthly_payment))
    months_out: list[dict[str, Any]] = []
    notes: list[str] = []

    if not line.active or rem <= ZERO:
        return {
            "line_id": line.id,
            "name": line.name,
            "months": [],
            "months_remaining": 0,
            "total_payments": "0.00",
            "final_month": None,
            "capped": False,
            "notes": ["Plan paid off or inactive."],
        }

    if pay <= ZERO:
        return {
            "line_id": line.id,
            "name": line.name,
            "months": [],
            "months_remaining": 0,
            "total_payments": "0.00",
            "final_month": None,
            "capped": False,
            "notes": [
                "Monthly payment is zero — set a monthly installment to project the calendar."
            ],
        }

    # First installment month: current calendar month if as_of is after start, else start month
    cursor = _first_of_month(as_of)
    if line.start_date and _first_of_month(line.start_date) > cursor:
        cursor = _first_of_month(line.start_date)

    remaining = rem
    total_paid = ZERO
    capped = False
    guard = 0
    while remaining > ZERO and guard < max_months:
        # Allow any payment whose month is on or before the plan end month
        if line.end_date is not None and _first_of_month(cursor) > _first_of_month(
            line.end_date
        ):
            notes.append(
                f"Stopped after plan end month {line.end_date.isoformat()[:7]} "
                f"with {remaining} still remaining."
            )
            break
        installment = _q(min(remaining, pay))
        remaining = _q(remaining - installment)
        total_paid += installment
        months_out.append(
            {
                "month": cursor.isoformat()[:7],  # YYYY-MM
                "month_start": cursor.isoformat(),
                "payment": str(installment),
                "principal_after": str(remaining),
                "is_final": remaining <= ZERO,
            }
        )
        if remaining <= ZERO:
            break
        cursor = _add_months(cursor, 1)
        guard += 1
    else:
        if remaining > ZERO and guard >= max_months:
            capped = True
            notes.append(
                f"Calendar capped at {max_months} months with {remaining} still remaining "
                "(increase monthly payment or check the plan)."
            )

    final = months_out[-1]["month"] if months_out else None
    return {
        "line_id": line.id,
        "name": line.name,
        "principal_remaining": str(rem),
        "monthly_payment": str(pay),
        "start_date": line.start_date.isoformat() if line.start_date else None,
        "end_date": line.end_date.isoformat() if line.end_date else None,
        "months": months_out,
        "months_remaining": len(months_out),
        "total_payments": str(_q(total_paid)),
        "final_month": final,
        "capped": capped,
        "notes": notes,
    }


def project_promo_calendar(
    session: Session,
    account_id: int,
    *,
    as_of: date | None = None,
    max_months: int = MAX_PROMO_CALENDAR_MONTHS,
    active_only: bool = True,
) -> dict[str, Any]:
    """Dynamic multi-line promo amortization calendar for a card.

    Not fixed at 12 months — each line runs until principal is cleared
    (or end_date / safety cap). Account-level rows merge all lines by month.
    """
    as_of = as_of or date.today()
    lines = list_promo_lines(session, account_id, active_only=active_only)
    line_schedules: list[dict[str, Any]] = []
    by_month: dict[str, dict[str, Any]] = {}

    for line in lines:
        # Project any active line with remaining balance (including future-start plans)
        if active_only and not line.active:
            continue
        if _d(line.principal_remaining) <= ZERO:
            continue
        if line.end_date is not None and line.end_date < as_of:
            continue
        sched = project_line_calendar(line, as_of=as_of, max_months=max_months)
        line_schedules.append(sched)
        for m in sched.get("months") or []:
            key = m["month"]
            bucket = by_month.setdefault(
                key,
                {
                    "month": key,
                    "payment_total": ZERO,
                    "principal_after_total": ZERO,
                    "lines": [],
                },
            )
            bucket["payment_total"] += _d(m["payment"])
            bucket["principal_after_total"] += _d(m["principal_after"])
            bucket["lines"].append(
                {
                    "line_id": sched["line_id"],
                    "name": sched["name"],
                    "payment": m["payment"],
                    "principal_after": m["principal_after"],
                    "is_final": m.get("is_final", False),
                }
            )

    merged = []
    for key in sorted(by_month.keys()):
        b = by_month[key]
        merged.append(
            {
                "month": key,
                "payment_total": str(_q(b["payment_total"])),
                "principal_after_total": str(_q(b["principal_after_total"])),
                "line_count": len(b["lines"]),
                "lines": b["lines"],
            }
        )

    max_span = max((s.get("months_remaining") or 0) for s in line_schedules) if line_schedules else 0
    any_capped = any(s.get("capped") for s in line_schedules)

    return {
        "account_id": account_id,
        "as_of": as_of.isoformat(),
        "line_count": len(line_schedules),
        "months_span": max_span,
        "max_months_cap": max_months,
        "capped": any_capped,
        "lines": line_schedules,
        "by_month": merged,
        "hint": (
            "Calendar length is dynamic from remaining ÷ monthly payment "
            f"(up to {max_months} months). 24-month financing shows 24 rows when payments match."
        ),
    }
