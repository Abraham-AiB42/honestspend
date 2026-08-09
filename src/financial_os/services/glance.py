"""Mobile / multi-client glance: Spendable + digest + rescue headline in one payload."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Transaction
from financial_os.services.digest import build_digest
from financial_os.services.ifpp_service import ifpp_to_dict, run_ifpp


def build_glance(
    session: Session,
    *,
    profile_id: int | None = None,
    scope: str | None = None,
    mode: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    ifpp = run_ifpp(session, as_of=as_of, mode=mode, profile_id=profile_id, scope=scope)
    dig = build_digest(session, as_of=as_of, profile_id=profile_id, scope=scope)
    ifpp_d = ifpp_to_dict(ifpp, session=session)

    # Pending exposure (not yet in Spendable balances if books lag)
    sc = ifpp_d.get("ifpp_scope") or scope or "entity"
    pid = ifpp_d.get("profile_id") or profile_id
    pq = session.query(Transaction).filter(
        Transaction.status == "pending",
        Transaction.is_transfer.is_(False),
    )
    if sc == "entity" and pid is not None:
        pq = pq.filter(Transaction.profile_id == pid)
    pending_rows = pq.all()
    pending_out = sum(
        (abs(Decimal(str(t.amount))) for t in pending_rows if Decimal(str(t.amount)) < 0),
        Decimal("0"),
    )
    pending_in = sum(
        (Decimal(str(t.amount)) for t in pending_rows if Decimal(str(t.amount)) > 0),
        Decimal("0"),
    )

    alerts = dig.get("alerts") or []
    top = alerts[:3]
    return {
        "as_of": as_of.isoformat(),
        "product": "Floatpile",
        "ifpp_scope": ifpp_d.get("ifpp_scope"),
        "profile_id": ifpp_d.get("profile_id"),
        "cash_spendable": ifpp_d["cash_spendable"],
        "card_float_interest_free": ifpp_d["card_float_interest_free"],
        "combined_purchasing_power": ifpp_d["combined_purchasing_power"],
        "next_red_day": ifpp_d.get("next_red_day"),
        "is_red_now": ifpp_d.get("is_red_now"),
        "ifpp_cleared_only": (ifpp_d.get("details") or {}).get("ifpp_cleared_only"),
        "pending": {
            "count": len(pending_rows),
            "outflows_abs": str(pending_out.quantize(Decimal("0.01"))),
            "inflows": str(pending_in.quantize(Decimal("0.01"))),
            "warning": (
                f"{len(pending_rows)} pending txn(s) · ${pending_out} outflow not fully trusted"
                if pending_rows
                else None
            ),
        },
        "digest_message": dig.get("message"),
        "headline": dig.get("headline"),
        "alerts": top,
        "minutes_needed": dig.get("minutes_needed"),
        "actions": [
            {"code": "rescue", "path": "POST /api/liquidity/rescue"},
            {"code": "brief", "path": "GET /api/digest/brief"},
            {"code": "buy", "path": "POST /api/pre-purchase"},
        ],
        "client_hint": "Mobile/Mac glance clients: poll this endpoint; do not reimplement IFPP.",
    }
