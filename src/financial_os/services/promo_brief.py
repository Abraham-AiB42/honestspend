"""0% promo set-aside brief for Simple Home (dream H1-C3)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import ScheduledItem
from financial_os.services.promo_clock import promo_death_clock


def build_promo_brief(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    clock = promo_death_clock(session, as_of=as_of, profile_id=profile_id)
    items = [
        p
        for p in (clock.get("items") or [])
        if p.get("urgency") in ("critical", "soon", "expired", "ok")
        and p.get("sinking_fund")
    ]
    # Prefer urgent first
    urgent = [p for p in items if p.get("urgency") in ("critical", "soon", "expired")]
    focus = urgent or items

    sq = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        sq = sq.filter(ScheduledItem.profile_id == profile_id)
    sinks = list(sq.filter(ScheduledItem.name.like("0% sink%")).all())

    needs: list[dict[str, Any]] = []
    for p in focus:
        nick = p.get("name") or ""
        has = any(nick and nick in (s.name or "") for s in sinks)
        if has:
            continue
        monthly = (p.get("sinking_fund") or {}).get("monthly")
        needs.append(
            {
                "account_id": p.get("account_id"),
                "name": nick,
                "days_left": p.get("days_left"),
                "monthly_sink": str(monthly),
                "promo_end": p.get("promo_end"),
                "urgency": p.get("urgency"),
                "reason": (
                    f"0% ends in {p.get('days_left')}d — set aside ${monthly}/mo "
                    f"so you never pay APR by accident."
                ),
            }
        )

    if not needs:
        return {
            "attention": "clear",
            "title": "0% promos on track",
            "reason": "No promo balloons need a new set-aside.",
            "primary_action": "hold",
            "button_label": "Done",
            "needs_attention": False,
            "items": [],
        }

    top = needs[0]
    return {
        "attention": "action",
        "title": f"Set aside for {top['name']}",
        "reason": top["reason"],
        "primary_action": "promo_sink",
        "button_label": f"Create ${top['monthly_sink']}/mo set-aside",
        "needs_attention": True,
        "account_id": top["account_id"],
        "items": needs,
    }
