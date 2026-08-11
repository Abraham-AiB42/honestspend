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
        monthly = (p.get("sinking_fund") or {}).get("monthly")
        try:
            monthly_d = float(monthly or 0)
        except (TypeError, ValueError):
            monthly_d = 0.0
        days = p.get("days_left")
        try:
            days_i = int(days) if days is not None else 0
        except (TypeError, ValueError):
            days_i = 0
        months_left = max(1, (days_i + 29) // 30) if days_i > 0 else 1
        # Existing sinks for this card nickname
        matching = [s for s in sinks if nick and nick in (s.name or "")]
        has = bool(matching)
        funded = 0.0
        for s in matching:
            try:
                funded += abs(float(s.amount or 0))
            except (TypeError, ValueError):
                pass
        underfunded = has and monthly_d > 0 and funded + 0.01 < monthly_d
        if has and not underfunded:
            continue
        if underfunded:
            reason = (
                f"0% ends in {days}d — set-aside ${funded:.0f}/mo is short of "
                f"${monthly_d:.0f}/mo needed to clear without APR."
            )
            urgency = "critical" if days_i <= 45 else (p.get("urgency") or "soon")
        else:
            reason = (
                f"0% ends in {p.get('days_left')}d — set aside ${monthly}/mo "
                f"so you never pay APR by accident."
            )
            urgency = p.get("urgency")
        needs.append(
            {
                "account_id": p.get("account_id"),
                "name": nick,
                "days_left": p.get("days_left"),
                "monthly_sink": str(monthly),
                "months_left_est": months_left,
                "promo_end": p.get("promo_end"),
                "urgency": urgency,
                "underfunded": underfunded,
                "reason": reason,
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
