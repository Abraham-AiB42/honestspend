"""Month-close checklist — QuickBooks job in Simple shell (dream H1-B)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import AppSettings
from financial_os.services.fee_brief import build_fee_brief
from financial_os.services.import_brief import build_import_brief
from financial_os.services.promo_clock import promo_death_clock
from financial_os.services.reconcile import reconcile_report
from financial_os.services.tax_vault import get_tax_vault


def period_key(as_of: date | None = None) -> str:
    d = as_of or date.today()
    return d.strftime("%Y-%m")


def get_month_close_state(session: Session) -> dict[str, Any]:
    s = session.get(AppSettings, 1)
    if not s:
        return {
            "period": period_key(),
            "closed_this_period": False,
            "last_closed_at": None,
            "last_closed_period": None,
        }
    cur = period_key()
    last_period = getattr(s, "month_close_period", None)
    last_at = getattr(s, "month_close_last_at", None)
    return {
        "period": cur,
        "closed_this_period": bool(last_period == cur),
        "last_closed_at": last_at.isoformat() if last_at else None,
        "last_closed_period": last_period,
    }


def mark_month_closed(
    session: Session,
    *,
    as_of: date | None = None,
    force: bool = False,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Record that the user finished the month-close ritual for as_of's period."""
    as_of = as_of or date.today()
    checklist = build_month_close(session, profile_id=profile_id, as_of=as_of)
    if not checklist.get("all_done") and not force:
        return {
            "ok": False,
            "error": "Required month-close steps still open.",
            "checklist": checklist,
        }
    s = session.get(AppSettings, 1)
    if not s:
        s = AppSettings(id=1)
        session.add(s)
        session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s.month_close_period = period_key(as_of)
    s.month_close_last_at = now
    session.flush()
    return {
        "ok": True,
        "period": s.month_close_period,
        "closed_at": now.isoformat(),
        "forced": force and not checklist.get("all_done"),
        "message": f"Month {s.month_close_period} marked closed. Open rarely until next period.",
        "checklist": build_month_close(session, profile_id=profile_id, as_of=as_of),
    }


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
        # Cash IFPP only — card/loan drift does not block Safe-to-spend month-close
        from financial_os.db import Account

        aq = session.query(Account).filter(Account.archived_at.is_(None))
        if profile_id is not None:
            aq = aq.filter(Account.profile_id == profile_id)
        cash_ifpp = [
            a
            for a in aq.all()
            if a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp
        ]
        missing_bank_bal = sum(1 for a in cash_ifpp if a.institution_balance is None)
        cash_drifted = 0
        for a in cash_ifpp:
            if a.institution_balance is None:
                continue
            books_bal = Decimal(str(a.current_balance or 0))
            inst_bal = Decimal(str(a.institution_balance))
            if abs(books_bal - inst_bal) >= Decimal("0.01"):
                cash_drifted += 1
        drifted = cash_drifted
    except Exception:
        drifted = 0
        missing_bank_bal = 0
        cash_ifpp = []

    # Reconcile: no cash drift. Once money-in habit started (any bank bal), require
    # every cash IFPP account to have an institution_balance (honesty gate).
    any_inst = any(a.institution_balance is not None for a in cash_ifpp) if cash_ifpp else False
    if drifted:
        reconcile_done = False
        recon_title = f"{drifted} cash account(s) with drift"
        recon_detail = "Books vs bank differ — Import → Set Safe to spend from bank."
    elif any_inst and missing_bank_bal:
        reconcile_done = False
        recon_title = f"{missing_bank_bal} cash account(s) need bank bal"
        recon_detail = (
            f"{missing_bank_bal} cash account(s) have no bank ending balance — "
            "import each cash account once (CSV/OFX) or enter ending bal."
        )
    else:
        reconcile_done = True
        recon_title = "Reconcile clean"
        recon_detail = (
            "Cash accounts match bank ending balances."
            if any_inst
            else (
                "No bank ending balances yet — import when ready (does not block close yet)."
                if cash_ifpp
                else "No cash accounts."
            )
        )

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
            "title": recon_title,
            "done": reconcile_done,
            "action": "import" if (missing_bank_bal and not drifted) else ("reconcile" if not reconcile_done else "hold"),
            "detail": recon_detail,
            "button_label": (
                "Import bank bal"
                if missing_bank_bal and not drifted
                else ("Match bank" if not reconcile_done else "Reconcile")
            ),
            "drifted": drifted,
            "missing_bank_balance": missing_bank_bal,
        },
        {
            "id": "backup",
            "title": "Backup reminder",
            "done": False,  # filled below if period already closed
            "action": "backup",
            "detail": "Encrypted or local backup after month-close keeps the books safe.",
            "button_label": "Data & backup",
            "optional": True,
        },
    ]

    state = get_month_close_state(session)
    closed = bool(state.get("closed_this_period"))
    if closed:
        for s in steps:
            if s["id"] == "backup":
                s["done"] = True
                s["detail"] = (
                    f"Period {state.get('last_closed_period')} closed"
                    + (
                        f" · {state.get('last_closed_at')}"
                        if state.get("last_closed_at")
                        else ""
                    )
                )

    # Optional backup doesn't block "all done" for required steps
    required = [s for s in steps if not s.get("optional")]
    done_n = sum(1 for s in required if s["done"])
    total = len(required)
    all_done = done_n == total
    can_mark_closed = all_done and not closed

    if closed:
        subtitle = f"Period {state.get('period')} is closed — open rarely until next month."
        progress = f"Closed · {state.get('last_closed_period') or state.get('period')}"
    elif all_done:
        subtitle = "All required steps clear — take a backup, then mark the month closed."
        progress = f"{done_n} of {total} required done · ready to close"
    else:
        subtitle = "Tick these once a month so books stay trustworthy."
        progress = f"{done_n} of {total} required done"

    return {
        "title": "Close the month",
        "subtitle": subtitle,
        "as_of": as_of.isoformat(),
        "period": state.get("period"),
        "done_count": done_n,
        "total": total,
        "all_done": all_done,
        "closed_this_period": closed,
        "can_mark_closed": can_mark_closed,
        "last_closed_at": state.get("last_closed_at"),
        "last_closed_period": state.get("last_closed_period"),
        "progress_label": progress,
        "minutes": 10,
        "steps": steps,
        "promo_open_count": len(promo_open),
        "primary_action": (
            "mark_closed" if can_mark_closed else ("hold" if closed else None)
        ),
        "button_label": (
            "Mark month closed"
            if can_mark_closed
            else ("Closed this month" if closed else "Do next close step")
        ),
    }
