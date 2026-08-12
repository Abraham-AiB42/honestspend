"""Annual tax handoff checklist (dream H2-B) — prep, not e-file."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Profile, Transaction
from honestspend.services.tax_vault import get_tax_vault


def build_tax_year_checklist(
    session: Session,
    *,
    year: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    year = year or as_of.year
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    profiles = (
        session.query(Profile)
        .filter(Profile.archived_at.is_(None))
        .order_by(Profile.id)
        .all()
    )
    vault = get_tax_vault(session)
    has_biz = any(p.entity_type == "business" for p in profiles)

    steps: list[dict[str, Any]] = []
    uncat_total = 0
    for p in profiles:
        if p.entity_type == "child":
            continue
        uncat = (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == p.id,
                Transaction.txn_date >= start,
                Transaction.txn_date <= end,
                Transaction.category_id.is_(None),
                Transaction.is_transfer.is_(False),
                Transaction.status != "void",
            )
            .count()
        )
        uncat_total += uncat
        steps.append(
            {
                "id": f"categorize_{p.id}",
                "title": f"Categorize {p.display_name} ({year})",
                "done": uncat == 0,
                "action": "review",
                "detail": (
                    f"{uncat} uncategorized in {year} — Sort charges before packet."
                    if uncat
                    else f"All {year} charges categorized for {p.display_name}."
                ),
                "profile_id": p.id,
                "button_label": "Sort charges",
            }
        )

    # Tax vault
    bal = Decimal(str(vault.get("balance") or 0))
    if has_biz:
        tax_done = bal > 0 or not vault.get("enabled")
        tax_detail = (
            f"Vault ${bal} reserved."
            if bal > 0
            else "Business income usually needs estimated tax set-aside."
        )
    else:
        tax_done = True
        tax_detail = "Paycheck-only: vault optional if taxes already withheld."
    steps.append(
        {
            "id": "tax_vault",
            "title": "Tax set-aside",
            "done": tax_done,
            "action": "fund_tax_vault" if not tax_done else "hold",
            "detail": tax_detail,
            "button_label": "Tax vault",
        }
    )

    # Packet readiness cue
    steps.append(
        {
            "id": "export_packet",
            "title": f"Export {year} tax packet when ready",
            "done": uncat_total == 0 and tax_done,
            "action": "tax_packet",
            "detail": (
                "Full books → Tax packet / CPA pack for TurboTax or your CPA."
                if uncat_total == 0
                else "Finish categorizing first — packet quality follows books quality."
            ),
            "button_label": "Tax packet",
            "optional": True,
        }
    )

    # Multi-state note
    multi = any(getattr(p, "multi_state", False) for p in profiles)
    if multi:
        steps.append(
            {
                "id": "multi_state",
                "title": "Multi-state notes",
                "done": True,
                "action": "hold",
                "detail": "Profile flags multi-state — keep filing notes current under Full books.",
                "optional": True,
            }
        )

    required = [s for s in steps if not s.get("optional")]
    done_n = sum(1 for s in required if s["done"])
    total = len(required)
    return {
        "year": year,
        "title": f"Tax year {year}",
        "subtitle": (
            "Books look packet-ready — export when you file."
            if done_n == total
            else "Prep handoff for TurboTax/CPA — not e-file."
        ),
        "done_count": done_n,
        "total": total,
        "all_done": done_n == total,
        "progress_label": f"{done_n} of {total} required done",
        "steps": steps,
        "uncategorized_year": uncat_total,
        "has_business": has_biz,
        "disclaimer": "Educational bookkeeping prep only — not tax advice.",
    }
