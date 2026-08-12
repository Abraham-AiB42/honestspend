"""Daily digest — open rarely, still stay safe."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from honestspend.services.capital_desk import build_capital_desk
from honestspend.services.promo_clock import promo_death_clock
from honestspend.db import Transaction


def build_digest(
    session: Session,
    *,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    desk = build_capital_desk(session, as_of=as_of, profile_id=profile_id, scope=scope)
    sc = scope or desk.get("ifpp", {}).get("ifpp_scope") or "entity"
    # Resolve profile for entity silo counts
    resolved_pid = profile_id
    if sc == "entity" and resolved_pid is None:
        resolved_pid = desk.get("ifpp", {}).get("profile_id")

    promos = promo_death_clock(
        session, as_of=as_of, profile_id=resolved_pid if sc == "entity" else None, scope=sc
    )

    uncat_q = session.query(Transaction).filter(Transaction.category_id.is_(None))
    pending_q = session.query(Transaction).filter(Transaction.status == "pending")
    if sc == "entity" and resolved_pid is not None:
        uncat_q = uncat_q.filter(Transaction.profile_id == resolved_pid)
        pending_q = pending_q.filter(Transaction.profile_id == resolved_pid)
    uncat = uncat_q.count()
    pending = pending_q.count()

    critical_promos = [p for p in promos["items"] if p["urgency"] in ("critical", "soon", "expired")]

    alerts = []
    ifpp = desk["ifpp"]
    if ifpp.get("is_red_now"):
        alerts.append(
            {
                "level": "critical",
                "code": "red_now",
                "message": "Checking is negative or Safe to spend is already below $0 — open rescue options",
                "action": "rescue",
            }
        )
    if ifpp.get("next_red_day") and not ifpp.get("is_red_now"):
        alerts.append(
            {
                "level": "critical",
                "code": "red_day",
                "message": f"Next risk day {ifpp['next_red_day']} — protect checking",
                "action": "bills",
                "next_red_day": ifpp["next_red_day"],
            }
        )
    for w in ifpp.get("warnings") or []:
        if "CHECKING NEGATIVE" in w or "negative" in w.lower():
            if not any(a.get("code") == "red_now" for a in alerts):
                alerts.append(
                    {
                        "level": "critical",
                        "code": "neg_check",
                        "message": w,
                        "action": "rescue",
                    }
                )
    for p in critical_promos:
        alerts.append(
            {
                "level": "warn" if p["urgency"] != "expired" else "critical",
                "code": "promo",
                "message": (
                    f"{p['name']}: 0% ends in {p['days_left']}d — "
                    f"set aside ${p['sinking_fund']['monthly']}/mo"
                ),
                "action": "promo_sink",
                "account_id": p.get("account_id"),
            }
        )
    if uncat > 20:
        alerts.append(
            {
                "level": "info",
                "code": "uncategorized",
                "message": f"{uncat} charges need a category — Sort charges",
                "action": "review",
                "count": uncat,
            }
        )

    try:
        from honestspend.services.import_reminders import build_import_reminder

        ir = build_import_reminder(session, as_of=as_of)
        if ir.get("due") and ir.get("title"):
            alerts.append(
                {
                    "level": "info",
                    "code": "import_reminder",
                    "message": ir["title"] + " — " + (ir.get("reason") or "Import CSV/OFX"),
                    "action": "import",
                    "cadence": ir.get("cadence"),
                    "focus": ir.get("focus"),
                }
            )
    except Exception:
        pass
    if pending > 0:
        # Sum pending outflows for loud Spendable warning
        pend_rows = (
            session.query(Transaction)
            .filter(Transaction.status == "pending")
        )
        if sc == "entity" and resolved_pid is not None:
            pend_rows = pend_rows.filter(Transaction.profile_id == resolved_pid)
        from decimal import Decimal as D

        pend_out = sum(
            (
                abs(D(str(t.amount)))
                for t in pend_rows.all()
                if D(str(t.amount)) < 0
            ),
            D("0"),
        )
        level = "warn" if pend_out >= D("100") else "info"
        alerts.append(
            {
                "level": level,
                "code": "pending_txns",
                "message": (
                    f"{pending} pending · ${pend_out} outflow may clear soon — "
                    f"Spendable uses books balances; toggle cleared-only in Settings"
                ),
                "action": "ledger",
                "count": pending,
                "pending_outflows_abs": str(pend_out.quantize(D("0.01"))),
            }
        )

    try:
        from honestspend.services.fee_scan import scan_fees

        fees = scan_fees(
            session,
            days=45,
            limit=20,
            as_of=as_of,
            profile_id=resolved_pid if sc == "entity" else None,
        )
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
                    "action": "fees",
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
        "pending_count": pending,
        "alerts": alerts,
        "minutes_needed": 0 if not alerts else (2 if len(alerts) < 3 else 5),
        "message": (
            "All clear — no action required."
            if not alerts
            else f"{len(alerts)} item(s) need attention (~{2 if len(alerts) < 3 else 5} min)."
        ),
        "ifpp_scope": sc,
        "profile_id": resolved_pid,
    }
