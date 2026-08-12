"""Mark a scheduled expense occurrence as paid and advance the schedule."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Category, ScheduledItem, Transaction
from honestspend.engine.schedule_expand import next_occurrence
from honestspend.services.account_balance import apply_amount_to_account

ZERO = Decimal("0")
CENT = Decimal("0.01")
_CARD_ID_RE = re.compile(r"card_account_id=(\d+)")
_SCHEDULE_CONSUMED_RE = re.compile(r"schedule\s*#\s*(\d+)", re.I)
_MATCH_WINDOW_DAYS = 5
_AMOUNT_TOLERANCE = Decimal("0.02")
# Auto-import path requires name overlap; manual mark-paid allows weaker card-payment hints.
_MIN_SCORE_MANUAL = 3
_MIN_SCORE_AUTO = 4


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def _parse_card_account_id(notes: str | None) -> int | None:
    if not notes:
        return None
    m = _CARD_ID_RE.search(notes)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _is_card_payment_schedule(row: ScheduledItem) -> bool:
    name = (row.name or "").strip()
    if name.startswith("Card payment"):
        return True
    return _parse_card_account_id(row.notes) is not None


def _resolve_card_account_id(session: Session, row: ScheduledItem) -> int | None:
    """Resolve credit account for a Card payment schedule (notes or nickname)."""
    card_acct_id = _parse_card_account_id(row.notes)
    if card_acct_id is not None:
        return card_acct_id
    nick = (row.name or "").replace("Card payment ·", "").replace("Card payment", "").strip()
    if not nick:
        return None
    card = (
        session.query(Account)
        .filter(
            Account.kind == "credit",
            Account.archived_at.is_(None),
            Account.nickname == nick,
        )
        .first()
    )
    return int(card.id) if card else None


def _find_system_category(session: Session, code: str) -> int | None:
    cat = session.query(Category).filter(Category.code == code).first()
    return int(cat.id) if cat else None


def _txn_already_consumed(t: Transaction) -> bool:
    """True if this cash outflow was already used to settle a schedule."""
    memo = t.memo or ""
    if "Marked paid" in memo and _SCHEDULE_CONSUMED_RE.search(memo):
        return True
    if "schedule settled" in memo.lower():
        return True
    return False


def _token_overlap(a: str, b: str) -> bool:
    """Loose name match: any 4+ char token shared, or substring either way."""
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    stop = {"card", "payment", "the", "and", "for", "from", "auto", "autopay"}
    ta = {t for t in re.split(r"[^a-z0-9]+", a) if len(t) >= 4 and t not in stop}
    tb = {t for t in re.split(r"[^a-z0-9]+", b) if len(t) >= 4 and t not in stop}
    return bool(ta & tb)


def _find_matching_cash_txn(
    session: Session,
    *,
    account_id: int,
    amount: Decimal,
    pay_date: date,
    payee_hint: str | None = None,
    min_score: int = _MIN_SCORE_MANUAL,
    exclude_txn_ids: set[int] | None = None,
    allow_transfer: bool = False,
) -> Transaction | None:
    """Find an imported/manual cash outflow that already paid this bill.

    Requires a meaningful payee/name score (default ≥3) so two equal-dollar bills
    do not share one cash row. Skips void, already-settled, and transfer-paired
    rows unless allow_transfer (card-payment pairing of already-linked cash).
    """
    target = abs(_q(amount))
    if target <= ZERO:
        return None
    start = pay_date - timedelta(days=_MATCH_WINDOW_DAYS)
    end = pay_date + timedelta(days=_MATCH_WINDOW_DAYS)
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.status != "void",
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(40)
        .all()
    )
    hint = (payee_hint or "").lower().strip()
    exclude = exclude_txn_ids or set()
    best: Transaction | None = None
    best_score = -1
    for t in rows:
        if t.id in exclude:
            continue
        if _d(t.amount) >= ZERO:
            continue
        if abs(abs(_q(t.amount)) - target) > _AMOUNT_TOLERANCE:
            continue
        if _txn_already_consumed(t):
            continue
        # Do not reuse transfer-paired cash for a different bill
        if not allow_transfer and (t.is_transfer or t.transfer_pair_id is not None):
            continue

        payee = (t.payee or "").lower()
        memo = (t.memo or "").lower()
        score = 0
        if hint and _token_overlap(hint, payee):
            score += 4
        elif hint and _token_overlap(hint, memo):
            score += 3
        elif hint and (hint in payee or payee in hint):
            score += 4
        # Card payment schedules: bank often says "PAYMENT THANK YOU" / Visa
        if score == 0 and hint.startswith("card payment"):
            if any(k in payee or k in memo for k in ("payment", "autopay", "thank you", "card")):
                score += 3
            nick = hint.replace("card payment ·", "").replace("card payment", "").strip()
            if nick and _token_overlap(nick, payee):
                score += 2

        if score < min_score:
            continue
        if score > best_score:
            best_score = score
            best = t
    return best


def _tag_cash_settled(txn: Transaction, schedule_id: int) -> None:
    """Mark cash outflow as consumed by this schedule (prevents double-match)."""
    tag = f"schedule settled #{schedule_id}"
    memo = txn.memo or ""
    if tag not in memo and f"schedule #{schedule_id}" not in memo:
        txn.memo = (f"{memo} · {tag}".strip(" ·") if memo else tag)[:256]


def mark_schedule_paid(
    session: Session,
    scheduled_id: int,
    *,
    as_of: date | None = None,
    create_transaction: bool = True,
    confirm_unsafe: bool = False,
    match_existing: bool = True,
    min_match_score: int = _MIN_SCORE_MANUAL,
    exclude_txn_ids: set[int] | None = None,
    require_match_txn_id: int | None = None,
) -> dict[str, Any]:
    """Mark one occurrence of an active expense schedule as paid.

    For active ScheduledItem with amount < 0 (expense):
    - If create_transaction and account_id set:
      * Prefer matching an existing cash outflow (import) — do not double-post
      * Card payment: never double-apply credit if card payment already imported
      * apply_amount_to_account with session (never-neg + card recompute)
    - Advance next_date via next_occurrence(cadence)

    When require_match_txn_id is set (auto-advance path):
    - Only settle if that exact cash txn is matched
    - Never create a new cash transaction
    - May still link card side to the matched cash
    - Raises ValueError without mutating schedule/books if match fails

    Return {ok, scheduled_id, name, next_date, transaction_id?, ended?, matched?, ...}
    """
    row = session.get(ScheduledItem, scheduled_id)
    if not row:
        raise ValueError("Scheduled item not found")
    if not row.active:
        raise ValueError("Scheduled item is not active")

    amount = _d(row.amount)
    if amount >= ZERO:
        raise ValueError("Mark paid applies only to expense schedules (amount < 0)")

    pay_date = as_of if as_of is not None else row.next_date
    txn_id: int | None = None
    card_txn_id: int | None = None
    matched = False
    matched_txn_id: int | None = None
    card_matched = False
    is_card = _is_card_payment_schedule(row)

    # Capture settle base BEFORE any card apply / recompute can rewrite next_date.
    base = row.next_date
    if as_of is not None and base < as_of:
        base = as_of

    # require_match_txn_id forces match-only: never invent cash, never advance on miss
    force_match_id = require_match_txn_id
    if force_match_id is not None:
        match_existing = True

    if (create_transaction or force_match_id is not None) and row.account_id:
        acct = session.get(Account, row.account_id)
        if not acct:
            raise ValueError("Scheduled account not found")
        expense_amt = -abs(amount)

        existing = None
        if force_match_id is not None:
            # Exact-id path only: validate the candidate is still a valid cash match
            candidate = session.get(Transaction, force_match_id)
            if candidate is None:
                raise ValueError(
                    f"Required cash match txn #{force_match_id} not found"
                )
            if int(candidate.account_id) != int(row.account_id):
                raise ValueError(
                    f"Required cash match txn #{force_match_id} is on a different account"
                )
            if candidate.status == "void":
                raise ValueError(
                    f"Required cash match txn #{force_match_id} is void"
                )
            if _d(candidate.amount) >= ZERO:
                raise ValueError(
                    f"Required cash match txn #{force_match_id} is not an outflow"
                )
            if abs(abs(_q(candidate.amount)) - abs(_q(expense_amt))) > _AMOUNT_TOLERANCE:
                raise ValueError(
                    f"Required cash match txn #{force_match_id} amount does not match schedule"
                )
            if _txn_already_consumed(candidate):
                raise ValueError(
                    f"Required cash match txn #{force_match_id} already settled another schedule"
                )
            # Already transfer-paired → do not re-settle (would steal pair / double-advance)
            if candidate.transfer_pair_id is not None:
                raise ValueError(
                    f"Required cash match txn #{force_match_id} is already transfer-paired"
                )
            if exclude_txn_ids and force_match_id in exclude_txn_ids:
                raise ValueError(
                    f"Required cash match txn #{force_match_id} already used in this batch"
                )
            existing = candidate
        elif match_existing:
            # Search around pay_date and schedule next_date (import may land days earlier)
            for center in dict.fromkeys([pay_date, row.next_date]):
                if center is None:
                    continue
                existing = _find_matching_cash_txn(
                    session,
                    account_id=int(row.account_id),
                    amount=expense_amt,
                    pay_date=center,
                    payee_hint=row.name,
                    min_score=min_match_score,
                    exclude_txn_ids=exclude_txn_ids,
                    allow_transfer=False,
                )
                if existing is not None:
                    break

        if existing is not None:
            matched = True
            matched_txn_id = existing.id
            txn_id = existing.id
            _tag_cash_settled(existing, row.id)
            if is_card:
                cat_id = _find_system_category(session, "SYS_CC_PAYMENT")
                if cat_id:
                    existing.category_id = cat_id
                existing.is_transfer = True
                card_txn_id, card_matched = _ensure_card_payment_side(
                    session,
                    row=row,
                    cash_txn=existing,
                    pay_date=pay_date,
                    abs_amt=abs(expense_amt),
                    category_id=cat_id or existing.category_id,
                )
            session.flush()
        elif force_match_id is not None:
            # Should not reach here (we raise above), but belt-and-suspenders
            raise ValueError(
                f"Required cash match txn #{force_match_id} could not be settled"
            )
        elif create_transaction:
            txn_id, card_txn_id, card_matched = _create_payment_txns(
                session,
                row=row,
                cash_acct=acct,
                expense_amt=expense_amt,
                pay_date=pay_date,
                confirm_unsafe=confirm_unsafe,
                match_existing=match_existing,
            )

    # Single cadence step from original base only (card apply must not have stepped us).
    new_next = next_occurrence(base, row.cadence or "monthly")

    ended = False
    if row.end_date is not None and new_next > row.end_date:
        row.active = False
        row.ended_at = datetime.now()
        row.ended_reason = "paid through end"
        ended = True

    row.next_date = new_next
    session.flush()

    # After settle: refresh card caches/amount once (matched or new card leg),
    # then restore settled next_date so recompute cannot double-advance.
    if is_card:
        card_acct_id = _resolve_card_account_id(session, row)
        if card_acct_id is not None:
            from honestspend.services.autopay import recompute_card_payment_schedule

            recompute_card_payment_schedule(session, card_acct_id, as_of=pay_date)
            row.next_date = new_next
            if ended:
                row.active = False
            session.flush()

    out: dict[str, Any] = {
        "ok": True,
        "scheduled_id": row.id,
        "name": row.name,
        "next_date": new_next.isoformat(),
        "ended": ended,
        "matched": matched,
        "card_matched": card_matched,
    }
    if txn_id is not None:
        out["transaction_id"] = txn_id
    if matched_txn_id is not None:
        out["matched_transaction_id"] = matched_txn_id
    if card_txn_id is not None:
        out["card_transaction_id"] = card_txn_id
    return out


def _create_payment_txns(
    session: Session,
    *,
    row: ScheduledItem,
    cash_acct: Account,
    expense_amt: Decimal,
    pay_date: date,
    confirm_unsafe: bool,
    match_existing: bool = True,
) -> tuple[int | None, int | None, bool]:
    """Create cash (and optional card) txns; return (cash_id, card_id, card_matched)."""
    is_card_pay = _is_card_payment_schedule(row)
    cat_id = row.category_id
    if is_card_pay:
        cat_id = _find_system_category(session, "SYS_CC_PAYMENT") or cat_id

    # Card payment with only card import (no cash match): still avoid double-applying card.
    # Cash side is created; card side links or posts carefully below.
    cash_txn = Transaction(
        profile_id=row.profile_id,
        account_id=row.account_id,
        category_id=cat_id,
        txn_date=pay_date,
        amount=expense_amt,
        payee=row.name,
        memo=f"Marked paid · schedule #{row.id}",
        status="cleared",
        is_transfer=is_card_pay,
    )
    session.add(cash_txn)
    apply_amount_to_account(
        cash_acct,
        expense_amt,
        confirm_unsafe=confirm_unsafe,
        session=session,
    )
    session.flush()
    cash_id = cash_txn.id
    card_id: int | None = None
    card_matched = False

    if is_card_pay:
        card_id, card_matched = _ensure_card_payment_side(
            session,
            row=row,
            cash_txn=cash_txn,
            pay_date=pay_date,
            abs_amt=abs(expense_amt),
            category_id=cat_id,
            prefer_match=match_existing,
        )

    return cash_id, card_id, card_matched


def _find_existing_card_payment(
    session: Session,
    *,
    card_acct_id: int,
    abs_amt: Decimal,
    pay_date: date,
) -> Transaction | None:
    """Find +N payment on credit already imported (do not re-apply).

    Skips rows already transfer-paired or tagged as settled for a schedule so a
    second schedule cannot re-bind an already-paired card payment.
    Prefers unpaired positives.
    """
    start = pay_date - timedelta(days=_MATCH_WINDOW_DAYS)
    end = pay_date + timedelta(days=_MATCH_WINDOW_DAYS)
    target = abs(_q(abs_amt))
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.account_id == card_acct_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.status != "void",
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .all()
    )
    for t in rows:
        if _d(t.amount) <= ZERO:
            continue
        if abs(_q(t.amount) - target) > _AMOUNT_TOLERANCE:
            continue
        # Already paired to another cash leg — do not re-bind
        if t.transfer_pair_id is not None:
            continue
        # Tagged settled for a schedule (or "Marked paid · schedule #N")
        if _txn_already_consumed(t):
            continue
        memo = t.memo or ""
        if _SCHEDULE_CONSUMED_RE.search(memo):
            continue
        return t
    return None


def _link_transfer_pair(cash_txn: Transaction, card_txn: Transaction) -> None:
    cash_txn.is_transfer = True
    cash_txn.transfer_pair_id = card_txn.id
    card_txn.is_transfer = True
    card_txn.transfer_pair_id = cash_txn.id


def _ensure_card_payment_side(
    session: Session,
    *,
    row: ScheduledItem,
    cash_txn: Transaction,
    pay_date: date,
    abs_amt: Decimal,
    category_id: int | None,
    prefer_match: bool = True,
) -> tuple[int | None, bool]:
    """Link existing card payment or post new one. Never double-apply books.

    Returns (card_txn_id, matched_existing).
    """
    if cash_txn.transfer_pair_id:
        pair = session.get(Transaction, cash_txn.transfer_pair_id)
        if pair and pair.status != "void":
            return pair.id, True

    card_acct_id = _resolve_card_account_id(session, row)
    if card_acct_id is None:
        return None, False

    if prefer_match:
        existing = _find_existing_card_payment(
            session,
            card_acct_id=card_acct_id,
            abs_amt=abs_amt,
            pay_date=pay_date,
        )
        if existing is not None:
            _link_transfer_pair(cash_txn, existing)
            if category_id and not existing.category_id:
                existing.category_id = category_id
            session.flush()
            return existing.id, True

    # Post new credit payment + apply books once
    card = session.get(Account, card_acct_id)
    if not card or (card.kind or "") != "credit":
        return None, False

    pay_amt = abs(_q(abs_amt))
    card_txn = Transaction(
        profile_id=row.profile_id or card.profile_id,
        account_id=card.id,
        category_id=category_id,
        txn_date=pay_date,
        amount=pay_amt,
        payee=row.name or f"Payment · {card.nickname}",
        memo=f"Marked paid · schedule #{row.id} · card payment",
        status="cleared",
        is_transfer=True,
        transfer_pair_id=cash_txn.id,
    )
    session.add(card_txn)
    # Skip mid-flight card recompute: it rewrites this schedule's next_date, and
    # mark_schedule_paid advances once from the pre-apply base. Recompute runs
    # after settle (with next_date restored) so amount/caches stay honest.
    apply_amount_to_account(card, pay_amt, session=session, recompute=False)
    session.flush()
    cash_txn.transfer_pair_id = card_txn.id
    cash_txn.is_transfer = True
    session.flush()
    return card_txn.id, False


def advance_schedules_after_import(
    session: Session,
    *,
    account_id: int | None = None,
    profile_id: int | None = None,
    as_of: date | None = None,
    lookback_days: int = 14,
    auto_apply: bool = True,
) -> dict[str, Any]:
    """After bank import: high-confidence match cash outflows → advance schedules.

    Only advances when payee/name score is strong (auto threshold) so we do not
    settle the wrong bill. Card payment schedules also link card-side payments.
    """
    as_of = as_of or date.today()
    window_start = as_of - timedelta(days=max(1, lookback_days))

    q = session.query(ScheduledItem).filter(
        ScheduledItem.active.is_(True),
        ScheduledItem.amount < ZERO,
    )
    if profile_id is not None:
        q = q.filter(ScheduledItem.profile_id == profile_id)
    if account_id is not None:
        # Schedules funded from this cash account, or Card payment notes for this card.
        # Digit-safe: card_account_id=1 must not match card_account_id=12.
        # Prefer id followed by ';' (normal notes) or end-of-string (marker-only notes).
        q = q.filter(
            (ScheduledItem.account_id == account_id)
            | (ScheduledItem.notes.like(f"%card_account_id={account_id};%"))
            | (ScheduledItem.notes.endswith(f"card_account_id={account_id}"))
        )

    schedules = q.order_by(ScheduledItem.next_date.asc(), ScheduledItem.id.asc()).all()
    advanced: list[dict[str, Any]] = []
    suggested: list[dict[str, Any]] = []
    used_txn_ids: set[int] = set()

    for row in schedules:
        if not row.account_id:
            continue
        # Only consider schedules due near now or overdue into lookback
        if row.next_date > as_of + timedelta(days=3):
            continue
        if row.next_date < window_start - timedelta(days=30):
            # Very stale — still try once if amount matches recent import
            pass

        pay_date = row.next_date if row.next_date <= as_of else as_of
        # Prefer pay_date near as_of for matching window
        match_date = max(pay_date, as_of - timedelta(days=lookback_days))
        match_date = min(match_date, as_of)

        candidate = _find_matching_cash_txn(
            session,
            account_id=int(row.account_id),
            amount=_d(row.amount),
            pay_date=match_date,
            payee_hint=row.name,
            min_score=_MIN_SCORE_AUTO,
            exclude_txn_ids=used_txn_ids,
        )
        if candidate is None and row.next_date < as_of:
            # Retry centered on next_date for overdue
            candidate = _find_matching_cash_txn(
                session,
                account_id=int(row.account_id),
                amount=_d(row.amount),
                pay_date=row.next_date,
                payee_hint=row.name,
                min_score=_MIN_SCORE_AUTO,
                exclude_txn_ids=used_txn_ids,
            )
        if candidate is None:
            continue

        entry = {
            "scheduled_id": row.id,
            "name": row.name,
            "amount": str(_q(_d(row.amount))),
            "cash_txn_id": candidate.id,
            "payee": candidate.payee,
            "txn_date": candidate.txn_date.isoformat() if candidate.txn_date else None,
            "next_date_before": row.next_date.isoformat() if row.next_date else None,
        }
        if not auto_apply:
            suggested.append(entry)
            used_txn_ids.add(int(candidate.id))
            continue

        try:
            # Use schedule next_date (or txn date) so match window centers on the bill,
            # not "today" which can sit outside ±5d of the import.
            settle_on = row.next_date or as_of
            if candidate.txn_date is not None:
                settle_on = candidate.txn_date
            # Savepoint: if require_match fails or settle aborts, roll back all mutations
            with session.begin_nested():
                out = mark_schedule_paid(
                    session,
                    row.id,
                    as_of=settle_on,
                    create_transaction=True,
                    match_existing=True,
                    min_match_score=_MIN_SCORE_AUTO,
                    exclude_txn_ids=used_txn_ids,
                    require_match_txn_id=int(candidate.id),
                )
                if not out.get("matched"):
                    # require_match path should raise; treat as failure and roll back
                    raise ValueError("match not confirmed on settle")
            used_txn_ids.add(int(candidate.id))
            entry["next_date"] = out.get("next_date")
            entry["matched"] = out.get("matched")
            entry["ended"] = out.get("ended")
            advanced.append(entry)
        except Exception as e:
            entry["error"] = str(e)
            suggested.append(entry)

    return {
        "as_of": as_of.isoformat(),
        "account_id": account_id,
        "advanced_count": len(advanced),
        "suggested_count": len(suggested),
        "advanced": advanced,
        "suggested": suggested,
        "hint": (
            f"Bank matched {len(advanced)} bill(s) — removed from Coming up."
            if advanced
            else "No high-confidence bill matches after import."
        ),
    }
