"""Post-import / live-books brief — what changed and what to do next (dream H1-A1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, PlaidItem, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def build_import_brief(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    recent_days: int = 7,
) -> dict[str, Any]:
    """Snapshot for Simple Home after CSV/Plaid activity.

    Answers: any new charges to sort? pending $ risk? bank links healthy?
    """
    as_of = as_of or date.today()
    since = as_of - timedelta(days=max(1, recent_days))

    tq = session.query(Transaction)
    if profile_id is not None:
        tq = tq.filter(Transaction.profile_id == profile_id)

    uncat_q = tq.filter(Transaction.category_id.is_(None))
    uncat = uncat_q.count()

    pending_q = tq.filter(Transaction.status == "pending")
    pending_rows = pending_q.all()
    pending_count = len(pending_rows)
    pending_out = sum(
        (abs(_d(t.amount)) for t in pending_rows if _d(t.amount) < 0),
        ZERO,
    )

    recent_q = tq.filter(Transaction.txn_date >= since)
    recent_count = recent_q.count()
    recent_uncat = recent_q.filter(Transaction.category_id.is_(None)).count()

    # Sample recent uncategorized for UI (names only)
    samples: list[str] = []
    for t in (
        uncat_q.order_by(Transaction.txn_date.desc(), Transaction.id.desc()).limit(5).all()
    ):
        payee = (t.payee or "Charge").strip()
        samples.append(f"{payee} · ${_d(t.amount):.2f}")

    # Bank links
    pq = session.query(PlaidItem)
    if profile_id is not None:
        pq = pq.filter(PlaidItem.profile_id == profile_id)
    items = pq.all()
    linked = len(items)
    needs_reauth = sum(1 for it in items if (it.status or "").lower() in ("login_required", "reauth", "error"))
    stale = 0
    cutoff = datetime.now() - timedelta(days=3)
    for it in items:
        if it.last_synced_at is None:
            stale += 1
            continue
        ts = it.last_synced_at
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)
        if ts < cutoff:
            stale += 1

    # Has any cash account? (for “link bank” soft tip)
    aq = session.query(Account).filter(Account.archived_at.is_(None))
    if profile_id is not None:
        aq = aq.filter(Account.profile_id == profile_id)
    has_cash = any(
        a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp for a in aq.all()
    )

    # Attention level for Simple card
    if needs_reauth:
        attention = "action"
        title = "Bank needs re-connect"
        reason = "A linked bank login expired — re-auth so books stay live."
        primary_action = "plaid"
        button = "Fix bank link"
    elif uncat > 0:
        attention = "action"
        title = f"{uncat} charge{'s' if uncat != 1 else ''} to sort"
        reason = "Uncategorized charges make tax and reports fuzzy. Accept categories in Sort charges."
        primary_action = "review"
        button = "Sort charges"
    elif pending_count > 0 and pending_out >= Decimal("100"):
        attention = "watch"
        title = f"{pending_count} pending · ${pending_out.quantize(Decimal('0.01'))} out"
        reason = "Pending outflows may not be in Safe to spend yet — confirm they clear."
        primary_action = "ledger"
        button = "Review pending"
    elif stale and linked:
        attention = "watch"
        title = "Bank sync is stale"
        reason = "A linked bank has not synced in 3+ days. Sync or re-link."
        primary_action = "plaid"
        button = "Open banks"
    elif recent_count > 0 and recent_uncat == 0:
        attention = "clear"
        title = "Books look current"
        reason = f"{recent_count} charge{'s' if recent_count != 1 else ''} in the last {recent_days} days — categories filled."
        primary_action = "hold"
        button = "Done"
    elif not linked and has_cash:
        attention = "optional"
        title = "Optional: link a bank"
        reason = "Manual balances work. Link bank or Import CSV when you want less typing."
        primary_action = "plaid"
        button = "Link bank"
    else:
        attention = "clear"
        title = "No import queue"
        reason = "Nothing waiting from banks or files."
        primary_action = "hold"
        button = "Done"

    return {
        "attention": attention,
        "title": title,
        "reason": reason,
        "primary_action": primary_action,
        "button_label": button,
        "uncategorized_count": uncat,
        "pending_count": pending_count,
        "pending_outflows_abs": str(pending_out.quantize(Decimal("0.01"))),
        "recent_days": recent_days,
        "recent_txn_count": recent_count,
        "recent_uncategorized": recent_uncat,
        "sample_uncategorized": samples,
        "plaid_linked": linked,
        "plaid_needs_reauth": needs_reauth,
        "plaid_stale": stale,
        "has_cash": has_cash,
        "needs_attention": attention in ("action", "watch"),
    }
