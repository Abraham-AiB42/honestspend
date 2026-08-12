"""Mobile / multi-client glance: Safe to spend (Home parity) + digest in one payload."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Transaction
from honestspend.services.budget_service import budget_reserve_total
from honestspend.services.digest import build_digest
from honestspend.services.ifpp_service import ifpp_to_dict, run_ifpp

ZERO = Decimal("0")


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

    sc = (ifpp_d.get("ifpp_scope") or scope or "entity").lower()
    pid = ifpp_d.get("profile_id") or profile_id
    cash_raw = Decimal(str(ifpp_d.get("cash_spendable") or "0"))
    reserve = budget_reserve_total(
        session, profile_id=profile_id, as_of=as_of, scope=sc
    )
    safe = max(ZERO, cash_raw - reserve)
    card_float = Decimal(str(ifpp_d.get("card_float_interest_free") or "0"))

    # Pending exposure (not yet in Spendable balances if books lag)
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
        "product": "HonestSpend",
        "ifpp_scope": ifpp_d.get("ifpp_scope"),
        "profile_id": ifpp_d.get("profile_id"),
        # Home-parity primary number
        "safe_to_spend": str(safe.quantize(Decimal("0.01"))),
        "safe_to_spend_before_budgets": str(cash_raw.quantize(Decimal("0.01"))),
        "budget_reserve": str(reserve.quantize(Decimal("0.01"))),
        # Raw IFPP for power clients
        "cash_spendable": ifpp_d["cash_spendable"],
        "card_float_interest_free": ifpp_d["card_float_interest_free"],
        "combined_purchasing_power": str((safe + card_float).quantize(Decimal("0.01"))),
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
        "client_hint": (
            "Poll this endpoint; use safe_to_spend as the primary number "
            "(matches Simple Home after budget reserve). Do not reimplement IFPP."
        ),
    }
