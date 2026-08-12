"""Post-import / live-books brief — what changed and what to do next (dream H1-A1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, PlaidItem, PromoConflict, PromoInstallmentLine, Transaction

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

    # Cash IFPP books vs bank ending bal (honesty)
    aq = session.query(Account).filter(Account.archived_at.is_(None))
    if profile_id is not None:
        aq = aq.filter(Account.profile_id == profile_id)
    accounts = aq.all()
    has_cash = any(
        a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp for a in accounts
    )
    # Linked Plaid cash with no bank ending bal yet (sync never got balances.current)
    plaid_cash_missing_bal = sum(
        1
        for a in accounts
        if (a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp)
        and a.plaid_account_id
        and a.institution_balance is None
    )
    drift_rows: list[dict[str, Any]] = []
    for a in accounts:
        if not (a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp):
            continue
        if a.institution_balance is None:
            continue
        books = _d(a.current_balance)
        inst = _d(a.institution_balance)
        d = (books - inst).quantize(Decimal("0.01"))
        if abs(d) >= Decimal("0.01"):
            drift_rows.append(
                {
                    "account_id": a.id,
                    "nickname": a.nickname,
                    "books_balance": str(books),
                    "institution_balance": str(inst),
                    "drift": str(d),
                }
            )
    drift_rows.sort(key=lambda r: abs(Decimal(r["drift"])), reverse=True)
    drift_count = len(drift_rows)
    top_drift = drift_rows[0] if drift_rows else None

    # Promo terms + unresolved conflicts (statement apply / D10)
    promo_line_q = session.query(PromoInstallmentLine)
    profile_acct_ids: list[int] | None = None
    if profile_id is not None:
        profile_acct_ids = [
            int(a.id)
            for a in session.query(Account.id)
            .filter(Account.profile_id == profile_id, Account.archived_at.is_(None))
            .all()
        ]
        if profile_acct_ids:
            promo_line_q = promo_line_q.filter(
                PromoInstallmentLine.account_id.in_(profile_acct_ids)
            )
        else:
            promo_line_q = promo_line_q.filter(PromoInstallmentLine.id == -1)
    promos_found = promo_line_q.count()
    conflict_q = session.query(PromoConflict).filter(PromoConflict.resolved_at.is_(None))
    if profile_id is not None:
        line_ids = [
            int(lid)
            for (lid,) in session.query(PromoInstallmentLine.id)
            .filter(
                PromoInstallmentLine.account_id.in_(profile_acct_ids or [-1])
            )
            .all()
        ]
        if line_ids:
            conflict_q = conflict_q.filter(PromoConflict.line_id.in_(line_ids))
        else:
            conflict_q = conflict_q.filter(PromoConflict.id == -1)
    promo_conflicts = conflict_q.count()
    # Txn mismatches are ephemeral import next_steps (not persisted)
    txn_mismatches = 0

    # Attention: Safe-to-spend honesty before Sort charges (PRODUCT fiscal #1)
    set_books_account_id: int | None = None
    secondary_action: str | None = None
    secondary_label: str | None = None
    if needs_reauth:
        attention = "action"
        title = "Bank needs re-connect"
        reason = "A linked bank login expired — re-auth so books stay live."
        primary_action = "plaid"
        button = "Fix bank link"
    elif drift_count > 0 and top_drift is not None:
        attention = "action"
        title = (
            f"Books ≠ bank · {top_drift['nickname']}"
            if drift_count == 1
            else f"{drift_count} accounts: books ≠ bank"
        )
        reason = (
            f"Books ${top_drift['books_balance']} vs bank ${top_drift['institution_balance']} "
            f"(Δ ${top_drift['drift']}). One tap sets Safe to spend from bank."
        )
        primary_action = "set_books_from_bank"
        button = "Set Safe to spend from bank"
        set_books_account_id = int(top_drift["account_id"])
        if uncat > 0:
            secondary_action = "review"
            secondary_label = f"Sort {uncat} charge{'s' if uncat != 1 else ''}"
            reason += f" Then sort {uncat} uncategorized charge{'s' if uncat != 1 else ''}."
    elif plaid_cash_missing_bal > 0:
        # Honesty: linked cash without bank bal ranks above Sort charges
        attention = "action"
        title = (
            f"{plaid_cash_missing_bal} linked cash account(s) need bank bal"
            if plaid_cash_missing_bal > 1
            else "Linked cash needs bank bal"
        )
        reason = (
            "Plaid did not return a current balance for linked cash — sync again "
            "or import CSV/OFX so Safe to spend has bank truth."
        )
        primary_action = "plaid"
        button = "Open banks"
        if uncat > 0:
            secondary_action = "review"
            secondary_label = f"Sort {uncat} charge{'s' if uncat != 1 else ''}"
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
        title = "Optional: import bank file"
        reason = "Manual balances work. Import CSV/OFX when you want less typing."
        primary_action = "import"
        button = "Import"
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
        "secondary_action": secondary_action,
        "secondary_label": secondary_label,
        "account_id": set_books_account_id,
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
        "plaid_cash_missing_bank_bal": plaid_cash_missing_bal,
        "has_cash": has_cash,
        "drift_count": drift_count,
        "drift_accounts": drift_rows[:5],
        "needs_attention": attention in ("action", "watch"),
        "promos_found": promos_found,
        "promo_conflicts": promo_conflicts,
        "txn_mismatches": txn_mismatches,
    }


def build_post_import_next_steps(
    session: Session,
    *,
    profile_id: int,
    account_id: int | None = None,
    created: int = 0,
    categorized: int = 0,
    skipped_existing: int = 0,
    drift: Decimal | None = None,
    books_balance: str | None = None,
    institution_balance: str | None = None,
    source: str = "import",
    promos_found: int = 0,
    promo_conflicts: int = 0,
    txn_mismatches: list[dict[str, Any]] | int | None = None,
) -> list[dict[str, str]]:
    """Structured CTAs after CSV / OFX / PDF import (open-rarely habit).

    Each step: {action, label, detail, account_id?} where action includes
    review | set_books_from_bank | reconcile | home | hold | bills | enter_ending_bal
    | review_txn_mismatch | review_promo_conflict.
    """
    steps: list[dict[str, str]] = []
    mismatch_list: list[dict[str, Any]] = []
    if isinstance(txn_mismatches, list):
        mismatch_list = txn_mismatches
    elif isinstance(txn_mismatches, int) and txn_mismatches > 0:
        mismatch_list = [{"action": "review_txn_mismatch"}] * int(txn_mismatches)

    uncat = (
        session.query(Transaction)
        .filter(Transaction.profile_id == profile_id, Transaction.category_id.is_(None))
        .count()
    )

    # D12: amount/date mismatches before balance CTAs so books honesty is visible
    for m in mismatch_list:
        try:
            from honestspend.services.import_txn_review import review_step_from_mismatch

            steps.append(review_step_from_mismatch(m if isinstance(m, dict) else {}))
        except Exception:
            steps.append(
                {
                    "action": "review_txn_mismatch",
                    "label": "Review transaction mismatch",
                    "detail": "Statement amount/date differs from books — books were kept.",
                }
            )

    if promo_conflicts and promo_conflicts > 0:
        steps.append(
            {
                "action": "review_promo_conflict",
                "label": f"{promo_conflicts} promo conflict{'s' if promo_conflicts != 1 else ''}",
                "detail": "Statement promo terms differ from what you entered — review before next payment.",
                "account_id": str(account_id) if account_id else "",
            }
        )
    elif promos_found and promos_found > 0:
        steps.append(
            {
                "action": "hold",
                "label": f"{promos_found} promo term{'s' if promos_found != 1 else ''} applied",
                "detail": "Installment / intro terms from statement update next payment.",
                "account_id": str(account_id) if account_id else "",
            }
        )

    # Highest priority for honesty: bank ending bal vs books after import
    if (
        drift is not None
        and abs(drift) >= Decimal("0.01")
        and institution_balance is not None
        and account_id is not None
    ):
        steps.append(
            {
                "action": "set_books_from_bank",
                "label": "Set Safe to spend from bank",
                "detail": (
                    f"Books ${books_balance or '?'} vs bank ${institution_balance} "
                    f"(Δ ${drift.quantize(Decimal('0.01'))}). One tap updates Safe to spend."
                ),
                "account_id": str(account_id),
            }
        )
    elif drift is not None and abs(drift) < Decimal("0.01") and institution_balance is not None:
        steps.append(
            {
                "action": "hold",
                "label": "Balances match",
                "detail": "Books match bank ending balance — Safe to spend is honest.",
            }
        )
    elif drift is not None and abs(drift) >= Decimal("0.01"):
        steps.append(
            {
                "action": "reconcile",
                "label": "Match bank balance",
                "detail": f"Books vs bank differ by ${drift.quantize(Decimal('0.01'))}.",
                "account_id": str(account_id) if account_id else "",
            }
        )
    elif institution_balance is None and account_id is not None and (
        created > 0 or skipped_existing > 0
    ):
        # New rows or skip-only reimport — still need bank bal when account has none
        acct_row = session.get(Account, account_id)
        if acct_row is not None and acct_row.institution_balance is None:
            steps.append(
                {
                    "action": "enter_ending_bal",
                    "label": "Enter bank ending balance",
                    "detail": (
                        "No Balance/LEDGERBAL in file — type statement ending bal so Safe to spend stays honest."
                    ),
                    "account_id": str(account_id),
                }
            )

    if uncat > 0:
        steps.append(
            {
                "action": "review",
                "label": "Sort charges",
                "detail": f"{uncat} uncategorized — accept categories so tax/reports stay clean.",
            }
        )
    elif created and categorized:
        steps.append(
            {
                "action": "hold",
                "label": "Categories filled",
                "detail": f"{categorized} auto-categorized from rules.",
            }
        )

    # Soft tip: possible bills/subscriptions from history (do not replace Sort charges)
    if uncat == 0 and created > 0:
        try:
            from honestspend.services.recurring_detect import detect_recurring

            rec = detect_recurring(session, profile_id=profile_id, limit=3)
            n = int(rec.get("count") or len(rec.get("suggestions") or []))
            if n > 0:
                sample = ""
                sug = rec.get("suggestions") or []
                if sug:
                    sample = sug[0].get("payee") or sug[0].get("name") or ""
                steps.append(
                    {
                        "action": "bills",
                        "label": f"{n} possible bill(s)",
                        "detail": (
                            f"Pattern like {sample} — confirm on Home / Possible bills."
                            if sample
                            else "Confirm recurring on Home if they look real."
                        ),
                    }
                )
        except Exception:
            pass

    if not steps:
        if created:
            steps.append(
                {
                    "action": "home",
                    "label": "Done",
                    "detail": f"{created} transaction(s) from {source}. Safe to spend will refresh on Home.",
                }
            )
        else:
            steps.append(
                {
                    "action": "hold",
                    "label": "No new rows",
                    "detail": "Duplicates skipped. Drop a newer date range next time.",
                }
            )
    return steps
