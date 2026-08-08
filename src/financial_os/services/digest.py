"""Daily digest — open rarely, still stay safe."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from financial_os.services.capital_desk import build_capital_desk
from financial_os.services.promo_clock import promo_death_clock
from financial_os.db import Transaction


def build_digest(
    session: Session,
    *,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    desk = build_capital_desk(session, as_of=as_of, profile_id=profile_id, scope=scope)
    promos = promo_death_clock(session, as_of=as_of)
    uncat = (
        session.query(Transaction)
        .filter(Transaction.category_id.is_(None))
        .count()
    )
    pending = (
        session.query(Transaction)
        .filter(Transaction.status == "pending")
        .count()
    )
    critical_promos = [p for p in promos["items"] if p["urgency"] in ("critical", "soon", "expired")]

    alerts = []
    if desk["ifpp"].get("next_red_day"):
        alerts.append(
            {
                "level": "critical",
                "code": "red_day",
                "message": f"Red day {desk['ifpp']['next_red_day']} — protect checking",
            }
        )
    for w in desk["ifpp"].get("warnings") or []:
        if "CHECKING NEGATIVE" in w or "negative" in w.lower():
            alerts.append({"level": "critical", "code": "neg_check", "message": w})
    for p in critical_promos:
        alerts.append(
            {
                "level": "warn" if p["urgency"] != "expired" else "critical",
                "code": "promo",
                "message": f"{p['name']}: 0% ends in {p['days_left']}d — sink ${p['sinking_fund']['monthly']}/mo",
            }
        )
    if uncat > 20:
        alerts.append(
            {
                "level": "info",
                "code": "uncategorized",
                "message": f"{uncat} uncategorized transactions — Review tab",
            }
        )
    if pending > 0:
        alerts.append(
            {
                "level": "info",
                "code": "pending_txns",
                "message": f"{pending} pending transaction(s) — confirm clears before trusting books drift",
            }
        )

    try:
        from financial_os.services.fee_scan import scan_fees

        fees = scan_fees(session, days=45, limit=20, as_of=as_of)
        if fees["count"] > 0:
            alerts.append(
                {
                    "level": "warn" if fees["count"] < 5 else "critical",
                    "code": "fee_detected",
                    "message": (
                        f"{fees['count']} possible fee(s) in 45d · "
                        f"${fees['total_abs']} total — open Fees / Review"
                    ),
                    "count": fees["count"],
                    "total_abs": fees["total_abs"],
                }
            )
    except Exception:
        pass

    return {
        "as_of": as_of.isoformat(),
        "headline": desk["headline"],
        "ifpp": desk["ifpp"],
        "opportunity": desk.get("opportunity"),
        "promo_clock": {
            "count": promos["count"],
            "critical": critical_promos,
        },
        "uncategorized_count": uncat,
        "alerts": alerts,
        "minutes_needed": 0 if not alerts else (2 if len(alerts) < 3 else 5),
        "message": (
            "All clear — no action required."
            if not alerts
            else f"{len(alerts)} item(s) need attention (~{2 if len(alerts) < 3 else 5} min)."
        ),
    }
