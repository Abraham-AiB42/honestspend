"""Month-close checklist — QuickBooks job in Simple shell (dream H1-B)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.services.fee_brief import build_fee_brief
from financial_os.services.import_brief import build_import_brief
from financial_os.services.promo_clock import promo_death_clock
from financial_os.services.reconcile import reconcile_report
from financial_os.services.tax_vault import get_tax_vault


def build_month_close(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Guided monthly hygiene: fees · charges · promo · tax · reconcile · backup cue."""
    as_of = as_of or date.today()
    books = build_import_brief(session, profile_id=profile_id, as_of=as_of)
    fees = build_fee_brief(session, profile_id=profile_id, as_of=as_of)
    promos = promo_death_clock(session, as_of=as_of, profile_id=profile_id)
    critical_promos = [
        p
        for p in (promos.get("items") or [])
        if p.get("urgency") in ("critical", "soon", "expired")
    ]
    # Has sink schedule already? detect by name prefix on any critical promo
    from financial_os.db import ScheduledItem

    sq = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        sq = sq.filter(ScheduledItem.profile_id == profile_id)
    sink_names = {s.name for s in sq.all() if s.name and s.name.startswith("0% sink")}

    promo_open = []
    for p in critical_promos:
        name = f"0% sink · {p.get('name', '')}"
        # match clock name field
        nick = p.get("name") or ""
        has_sink = any(nick and nick in sn for sn in sink_names) or any(
            sn.endswith(nick) for sn in sink_names if nick
        )
        if not has_sink:
            promo_open.append(p)

    vault = get_tax_vault(session)
    tax_done = (not vault.get("enabled")) or Decimal(str(vault.get("balance") or 0)) > 0
    # Business soft: if no business, tax optional done
    from financial_os.db import Profile

    has_biz = (
        session.query(Profile)
        .filter(Profile.entity_type == "business", Profile.archived_at.is_(None))
        .count()
        > 0
    )
    if not has_biz and vault.get("enabled") and Decimal(str(vault.get("balance") or 0)) == 0:
        tax_done = True  # personal W-2: optional
        tax_detail = "Optional for paycheck-only — skip if taxes already withheld."
    elif tax_done:
        tax_detail = f"Tax set-aside ${vault.get('balance', '0')} kept out of Safe to spend."
    else:
        tax_detail = "Business / unwithheld income — fund tax set-aside so Safe to spend stays honest."

    try:
        rep = reconcile_report(session, profile_id=profile_id)
        drifted = int(rep.get("drifted") or 0)
    except Exception:
        drifted = 0

    steps = [
        {
            "id": "fees",
            "title": fees.get("title") or "Fees",
            "done": not fees.get("needs_attention"),
            "action": "fees",
            "detail": fees.get("reason") or "",
            "button_label": "Review fees",
        },
        {
            "id": "sort_charges",
            "title": (
                f"Sort charges ({books.get('uncategorized_count', 0)})"
                if books.get("uncategorized_count")
                else "Charges categorized"
            ),
            "done": int(books.get("uncategorized_count") or 0) == 0,
            "action": "review",
            "detail": books.get("reason") or "",
            "button_label": "Sort charges",
        },
        {
            "id": "promo",
            "title": (
                f"{len(promo_open)} promo set-aside(s) needed"
                if promo_open
                else "0% promos on track"
            ),
            "done": len(promo_open) == 0,
            "action": "promo_sink",
            "detail": (
                f"{promo_open[0].get('name')}: set aside ${promo_open[0].get('sinking_fund', {}).get('monthly')}/mo"
                if promo_open
                else "No urgent 0% balloons without a set-aside."
            ),
            "button_label": "Create set-aside",
            "account_id": promo_open[0].get("account_id") if promo_open else None,
        },
        {
            "id": "tax",
            "title": "Tax set-aside" if not tax_done else "Tax set-aside OK",
            "done": tax_done,
            "action": "fund_tax_vault" if not tax_done else "hold",
            "detail": tax_detail,
            "button_label": "Set aside for taxes",
        },
        {
            "id": "reconcile",
            "title": f"{drifted} account(s) with drift" if drifted else "Reconcile clean",
            "done": drifted == 0,
            "action": "reconcile" if drifted else "hold",
            "detail": (
                "Books vs bank differ — resolve so Safe to spend is real."
                if drifted
                else "No institution drift flagged."
            ),
            "button_label": "Reconcile",
        },
        {
            "id": "backup",
            "title": "Backup reminder",
            "done": False,  # always soft reminder once per close pass
            "action": "backup",
            "detail": "Encrypted or local backup after month-close keeps the books safe.",
            "button_label": "Data & backup",
            "optional": True,
        },
    ]

    # Optional backup doesn't block "all done" for required steps
    required = [s for s in steps if not s.get("optional")]
    done_n = sum(1 for s in required if s["done"])
    total = len(required)
    all_done = done_n == total

    return {
        "title": "Close the month",
        "subtitle": (
            "All required steps clear — take a backup and close."
            if all_done
            else "Tick these once a month so books stay trustworthy."
        ),
        "as_of": as_of.isoformat(),
        "done_count": done_n,
        "total": total,
        "all_done": all_done,
        "progress_label": f"{done_n} of {total} required done",
        "minutes": 10,
        "steps": steps,
        "promo_open_count": len(promo_open),
    }
