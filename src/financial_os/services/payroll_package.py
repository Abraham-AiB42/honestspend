"""Thin payroll-day package: net pay + employer tax as linked expense schedules.

No employee table / payroll engine — two recurring cash outflows on the same
pay date and cadence so Coming up / IFPP stay honest on pay day.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, ScheduledItem

ZERO = Decimal("0")
_ALLOWED_CADENCE = frozenset({"biweekly", "semimonthly", "monthly", "weekly"})


def create_payroll_package(
    session: Session,
    *,
    profile_id: int,
    name: str,
    pay_date: date,
    cadence: str,
    net_payroll: Decimal,
    employer_tax: Decimal,
    cash_account_id: int,
    series_label: str | None = None,
) -> dict:
    """
    Create two expense schedules with the same next_date / cadence / account:
      1) name="{name} · net" amount=-net
      2) name="{name} · employer tax" amount=-tax

    Both notes include package_id=<uuid> so the pair can be found later.
    Returns {package_id, net_id, tax_id}.
    """
    if not session.get(Profile, profile_id):
        raise ValueError("Profile not found")

    acct = session.get(Account, cash_account_id)
    if not acct:
        raise ValueError("cash_account_id not found")
    if int(acct.profile_id) != int(profile_id):
        raise ValueError("cash_account_id must belong to the same profile")

    cad = (cadence or "biweekly").lower().strip()
    if cad not in _ALLOWED_CADENCE:
        raise ValueError(
            "cadence must be biweekly, semimonthly, monthly, or weekly"
        )

    net = abs(Decimal(str(net_payroll)))
    tax = abs(Decimal(str(employer_tax)))
    if net <= ZERO and tax <= ZERO:
        raise ValueError("net_payroll or employer_tax must be positive")

    base = (name or "").strip() or "Payroll"
    label = (series_label or base).strip()[:128]
    package_id = uuid4().hex
    note = f"package_id={package_id}"

    net_row = ScheduledItem(
        profile_id=profile_id,
        account_id=cash_account_id,
        name=f"{base} · net"[:128],
        amount=-net if net > ZERO else ZERO,
        next_date=pay_date,
        start_date=pay_date,
        cadence=cad,
        certainty="fixed",
        kind="expense",
        series_label=label,
        notes=note,
        active=True,
    )
    tax_row = ScheduledItem(
        profile_id=profile_id,
        account_id=cash_account_id,
        name=f"{base} · employer tax"[:128],
        amount=-tax if tax > ZERO else ZERO,
        next_date=pay_date,
        start_date=pay_date,
        cadence=cad,
        certainty="fixed",
        kind="expense",
        series_label=label,
        notes=note,
        active=True,
    )
    session.add(net_row)
    session.add(tax_row)
    session.flush()

    return {
        "package_id": package_id,
        "net_id": int(net_row.id),
        "tax_id": int(tax_row.id),
    }
