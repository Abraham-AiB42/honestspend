"""Reports that matter (dream H2-A) — not full QB suite."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def cashflow_by_entity(
    session: Session,
    *,
    days: int = 30,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Inflows / outflows / net per entity for lookback window."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))
    profiles = (
        session.query(Profile)
        .filter(Profile.archived_at.is_(None))
        .order_by(Profile.id)
        .all()
    )
    rows: list[dict[str, Any]] = []
    total_in = ZERO
    total_out = ZERO

    for p in profiles:
        tq = session.query(Transaction).filter(
            Transaction.profile_id == p.id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.status != "void",
            Transaction.is_transfer.is_(False),
        )
        inflow = ZERO
        outflow = ZERO
        for t in tq.all():
            a = _d(t.amount)
            if a > 0:
                inflow += a
            elif a < 0:
                outflow += abs(a)
        net = inflow - outflow
        total_in += inflow
        total_out += outflow
        # Cash balances now
        cash = sum(
            (
                _d(a.current_balance)
                for a in session.query(Account)
                .filter(
                    Account.profile_id == p.id,
                    Account.archived_at.is_(None),
                )
                .all()
                if a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp
            ),
            ZERO,
        )
        rows.append(
            {
                "profile_id": p.id,
                "display_name": p.display_name,
                "entity_type": p.entity_type,
                "inflow": str(inflow.quantize(Decimal("0.01"))),
                "outflow": str(outflow.quantize(Decimal("0.01"))),
                "net": str(net.quantize(Decimal("0.01"))),
                "cash_balance": str(cash.quantize(Decimal("0.01"))),
            }
        )

    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "start": start.isoformat(),
        "entities": rows,
        "totals": {
            "inflow": str(total_in.quantize(Decimal("0.01"))),
            "outflow": str(total_out.quantize(Decimal("0.01"))),
            "net": str((total_in - total_out).quantize(Decimal("0.01"))),
        },
        "title": f"Cash flow by who ({days}d)",
        "principle": "Siloed piles — Personal / Business / Child stay clear.",
    }


def fee_year_summary_report(
    session: Session,
    *,
    days: int = 365,
    profile_id: int | None = None,
) -> dict[str, Any]:
    from financial_os.services.fee_scan import fee_summary

    return fee_summary(session, days=days, profile_id=profile_id)
