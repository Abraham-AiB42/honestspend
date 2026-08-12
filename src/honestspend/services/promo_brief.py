"""0% promo set-aside brief for Simple Home (dream H1-C3)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, PromoInstallmentLine, ScheduledItem
from honestspend.services.promo_clock import promo_death_clock
from honestspend.services.promo_upsert import list_open_conflicts


def build_promo_brief(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    clock = promo_death_clock(session, as_of=as_of, profile_id=profile_id)
    # Ending within 45d (or already expired) — death-clock critical/soon/expired
    items = [
        p
        for p in (clock.get("items") or [])
        if p.get("urgency") in ("critical", "soon", "expired")
        and p.get("sinking_fund")
    ]
    # Prefer urgent first
    urgent = [p for p in items if p.get("urgency") in ("critical", "soon", "expired")]
    focus = urgent or items

    sq = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        sq = sq.filter(ScheduledItem.profile_id == profile_id)
    sinks = list(sq.filter(ScheduledItem.name.like("0% sink%")).all())

    profile_account_ids: set[int] | None = None
    if profile_id is not None:
        profile_account_ids = {
            int(r[0])
            for r in session.query(Account.id)
            .filter(Account.profile_id == profile_id, Account.archived_at.is_(None))
            .all()
        }

    conflicts_raw = list_open_conflicts(session)
    if profile_account_ids is not None:
        line_ids = {int(c["line_id"]) for c in conflicts_raw if c.get("line_id") is not None}
        line_acct: dict[int, int | None] = {}
        if line_ids:
            for lid, aid in (
                session.query(PromoInstallmentLine.id, PromoInstallmentLine.account_id)
                .filter(PromoInstallmentLine.id.in_(line_ids))
                .all()
            ):
                line_acct[int(lid)] = int(aid) if aid is not None else None
        conflicts_raw = [
            c
            for c in conflicts_raw
            if line_acct.get(int(c["line_id"])) in profile_account_ids
            or line_acct.get(int(c["line_id"])) is None
        ]
    open_conflict_count = len(conflicts_raw)

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
        # Ending within 45 days (or expired) only
        if days_i > 45:
            continue
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

    if open_conflict_count > 0 and not needs:
        return {
            "attention": "action",
            "title": "Review promo statement conflicts",
            "reason": (
                f"{open_conflict_count} unreviewed promo conflict"
                f"{'s' if open_conflict_count != 1 else ''} — "
                "keep yours or take the statement before promos hit Safe."
            ),
            "primary_action": "promo_conflict",
            "button_label": "Review promo conflicts",
            "needs_attention": True,
            "open_conflict_count": open_conflict_count,
            "account_id": None,
            "items": [],
        }

    if not needs:
        return {
            "attention": "clear",
            "title": "0% promos on track",
            "reason": "No promo balloons need a new set-aside.",
            "primary_action": "hold",
            "button_label": "Done",
            "needs_attention": False,
            "open_conflict_count": open_conflict_count,
            "items": [],
        }

    top = needs[0]
    reason = top["reason"]
    if open_conflict_count > 0:
        reason = (
            f"{reason} Also {open_conflict_count} unreviewed promo conflict"
            f"{'s' if open_conflict_count != 1 else ''}."
        )
    return {
        "attention": "action",
        "title": f"Set aside for {top['name']}",
        "reason": reason,
        "primary_action": "promo_sink",
        "button_label": f"Create ${top['monthly_sink']}/mo set-aside",
        "needs_attention": True,
        "open_conflict_count": open_conflict_count,
        "account_id": top["account_id"],
        "items": needs,
    }
