"""Create a monthly sinking-fund bill for a 0% promo balloon."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, ScheduledItem
from financial_os.services.promo_clock import promo_death_clock

ZERO = Decimal("0")


def create_promo_sink_bill(
    session: Session,
    *,
    account_id: int,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    card = session.get(Account, account_id)
    if not card or card.kind != "credit":
        raise ValueError("Credit account not found")

    clock = promo_death_clock(session, as_of=as_of)
    item = next((i for i in clock["items"] if i["account_id"] == account_id), None)
    if not item:
        raise ValueError("No active 0% promo on this account")

    monthly = Decimal(str(item["sinking_fund"]["monthly"]))
    if monthly <= ZERO:
        raise ValueError("Sinking fund amount is zero")

    name = f"0% sink · {card.nickname}"
    # Idempotent: reuse active schedule with same name + account
    existing = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == account_id,
            ScheduledItem.name == name,
        )
        .first()
    )
    if existing:
        existing.amount = -abs(monthly)
        existing.end_date = date.fromisoformat(item["promo_end"])
        existing.notes = f"Auto sink for 0% promo ending {item['promo_end']}"
        session.flush()
        return {
            "ok": True,
            "created": False,
            "updated": True,
            "scheduled_id": existing.id,
            "name": existing.name,
            "amount": str(existing.amount),
            "monthly_sink": str(monthly),
            "promo_end": item["promo_end"],
        }

    row = ScheduledItem(
        profile_id=card.profile_id,
        name=name,
        amount=-abs(monthly),
        next_date=as_of,
        end_date=date.fromisoformat(item["promo_end"]),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        account_id=account_id,
        notes=f"Auto sink for 0% promo ending {item['promo_end']} · balloon ${item['promo_balance']}",
        active=True,
    )
    session.add(row)
    session.flush()
    return {
        "ok": True,
        "created": True,
        "updated": False,
        "scheduled_id": row.id,
        "name": row.name,
        "amount": str(row.amount),
        "monthly_sink": str(monthly),
        "promo_end": item["promo_end"],
        "hint": "Recurring bill reduces Spendable so the balloon is funded before APR.",
    }
