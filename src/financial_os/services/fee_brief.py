"""Fee inbox summary for Simple Home (dream H1-C2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.services.fee_scan import scan_fees

ZERO = Decimal("0")


def build_fee_brief(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    days: int = 45,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    scan = scan_fees(session, days=days, limit=20, as_of=as_of, profile_id=profile_id)
    count = int(scan.get("count") or 0)
    total = scan.get("total_abs") or "0"
    samples: list[str] = []
    for f in (scan.get("candidates") or [])[:4]:
        if isinstance(f, dict):
            payee = f.get("payee") or "Fee"
            amt = f.get("abs_amount") or f.get("amount") or ""
            samples.append(f"{payee} · ${amt}")
        else:
            samples.append(str(f))

    if count <= 0:
        return {
            "attention": "clear",
            "title": "No fee alarms",
            "reason": f"No fee-like charges in the last {days} days.",
            "primary_action": "hold",
            "button_label": "Done",
            "count": 0,
            "total_abs": "0",
            "samples": [],
            "needs_attention": False,
            "days": days,
        }

    return {
        "attention": "action" if count >= 2 else "watch",
        "title": f"{count} possible fee{'s' if count != 1 else ''} (~${total})",
        "reason": "Fees are avoidable drag on Safe to spend — confirm or dismiss with eyes open.",
        "primary_action": "fees",
        "button_label": "Review fees",
        "count": count,
        "total_abs": str(total),
        "samples": samples,
        "needs_attention": True,
        "days": days,
    }
