"""Void of mark-paid card payment: schedule recomputes once (no double-step)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Account, Profile, ScheduledItem, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.autopay import recompute_card_payment_schedule
from honestspend.services.schedule_mark_paid import mark_schedule_paid
from honestspend.services.txn_void import void_transaction


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def _card_payment_setup(
    s,
    *,
    next_date: date = date(2026, 7, 15),
    card_bal: Decimal = Decimal("400.00"),
    pay_amount: Decimal = Decimal("-200.00"),
    policy: str = "statement",
):
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Pay from",
        current_balance=Decimal("1000.00"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=card_bal,
        credit_limit=Decimal("2000"),
        available_credit=Decimal("2000") - card_bal,
        is_cash_for_ifpp=False,
        autopay_policy=policy,
        payment_due_day=15,
        statement_close_day=1,
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=pay_amount,
        next_date=next_date,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy={policy}; timing=on_due; auto=statement_cycle",
    )
    s.add(item)
    s.commit()
    return p, cash, card, item


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def test_mark_paid_card_new_legs_next_date_plus_one_month(tmp_path: Path):
    """mark_schedule_paid card with new legs → next_date is original + 1 month."""
    s = _session(tmp_path)
    original = date(2026, 7, 15)
    _, cash, card, item = _card_payment_setup(s, next_date=original)

    out = mark_schedule_paid(s, item.id, as_of=original, create_transaction=True)
    s.commit()

    assert out["ok"] is True
    assert out.get("transaction_id")
    assert out.get("card_transaction_id")
    assert out.get("card_matched") is not True
    assert out["next_date"] == "2026-08-15"
    s.refresh(item)
    assert item.next_date == date(2026, 8, 15)
    assert item.active is True
    s.refresh(cash)
    s.refresh(card)
    assert Decimal(str(cash.current_balance)) == Decimal("800.00")
    assert Decimal(str(card.current_balance)) == Decimal("200.00")
    s.close()


def test_void_card_leg_after_mark_paid_recomputes_once(tmp_path: Path):
    """Void card payment txn after mark-paid → pair voided, schedule not double-stepped.

    Recompute once from projection (full rewrite OK). next_date must not land
    more than one extra month past an honest standalone recompute. Amount must
    match restored card balance (statement policy).
    """
    s = _session(tmp_path)
    original = date(2026, 7, 15)
    _, cash, card, item = _card_payment_setup(s, next_date=original)

    out = mark_schedule_paid(s, item.id, as_of=original, create_transaction=True)
    s.commit()
    assert out["next_date"] == "2026-08-15"
    card_txn_id = out["card_transaction_id"]
    cash_txn_id = out["transaction_id"]

    s.refresh(card)
    assert Decimal(str(card.current_balance)) == Decimal("200.00")

    vout = void_transaction(s, card_txn_id, reason="undo payment")
    s.commit()

    assert vout.get("recomputed_card_account_ids") == [card.id]
    assert vout.get("paired_transaction_id") == cash_txn_id
    assert vout.get("paired_voided") is True

    s.refresh(item)
    s.refresh(card)
    s.refresh(cash)
    # Both legs reversed → books restored
    assert Decimal(str(card.current_balance)) == Decimal("400.00")
    assert Decimal(str(cash.current_balance)) == Decimal("1000.00")
    card_txn = s.get(Transaction, card_txn_id)
    assert card_txn is not None
    assert card_txn.status == "void"
    cash_txn = s.get(Transaction, cash_txn_id)
    assert cash_txn is not None
    assert cash_txn.status == "void"

    # Honest recompute baseline (same books as after void)
    honest = recompute_card_payment_schedule(s, card.id, as_of=date(2026, 8, 11))
    s.flush()
    honest_due = _as_date(honest.get("next_due"))

    s.refresh(item)
    if not item.active:
        assert honest.get("schedule", {}).get("ended") or honest.get("policy") == "none"
    else:
        assert item.next_date is not None
        assert honest_due is not None
        ceiling = honest_due + relativedelta(months=1)
        assert item.next_date <= ceiling, (
            f"schedule next_date {item.next_date} double-stepped past "
            f"honest recompute due {honest_due} (ceiling {ceiling})"
        )
        # After void of payment, statement amount is full restored balance
        assert abs(Decimal(str(item.amount))) == Decimal("400.00")
        honest_amt = Decimal(str(honest.get("next_payment") or 0))
        if honest_amt > 0:
            assert abs(abs(Decimal(str(item.amount))) - honest_amt) < Decimal("0.02")
        # Prefer exact match to honest recompute (not one step past)
        assert item.next_date == honest_due

    s.close()


def test_void_cash_leg_after_mark_paid_voids_pair_and_recomputes_once(tmp_path: Path):
    """mark paid (both legs) → void cash → both void, schedule recomputed once.

    Transfer-pair void must not leave a half-void pair. Card books reverse with
    the paired leg; recompute runs once after both are status=void.
    """
    s = _session(tmp_path)
    original = date(2026, 7, 15)
    _, cash, card, item = _card_payment_setup(s, next_date=original)

    out = mark_schedule_paid(s, item.id, as_of=original, create_transaction=True)
    s.commit()
    assert out["next_date"] == "2026-08-15"
    cash_txn_id = out["transaction_id"]
    card_txn_id = out["card_transaction_id"]

    # Corrupt schedule as if a bad double-advance had happened
    s.refresh(item)
    item.next_date = date(2026, 10, 15)
    item.amount = Decimal("-1.00")
    s.commit()

    vout = void_transaction(s, cash_txn_id, reason="undo cash leg")
    s.commit()

    assert vout.get("recomputed_card_account_ids") == [card.id]
    assert vout.get("paired_transaction_id") == card_txn_id
    assert vout.get("paired_voided") is True

    s.refresh(item)
    s.refresh(cash)
    s.refresh(card)
    cash_txn = s.get(Transaction, cash_txn_id)
    card_txn = s.get(Transaction, card_txn_id)
    assert cash_txn is not None and cash_txn.status == "void"
    assert card_txn is not None and card_txn.status == "void"
    assert Decimal(str(cash.current_balance)) == Decimal("1000.00")
    # Paired card leg reversed with cash void
    assert Decimal(str(card.current_balance)) == Decimal("400.00")

    honest = recompute_card_payment_schedule(s, card.id, as_of=date(2026, 8, 11))
    s.flush()
    honest_due = _as_date(honest.get("next_due"))

    s.refresh(item)
    assert item.active is True
    assert item.next_date != date(2026, 10, 15), "stale double-step date must be rewritten"
    assert abs(Decimal(str(item.amount))) != Decimal("1.00")
    assert honest_due is not None
    assert item.next_date == honest_due
    honest_amt = Decimal(str(honest.get("next_payment") or 0))
    if honest_amt > 0:
        assert abs(Decimal(str(item.amount))) == honest_amt
    s.close()


def test_void_both_legs_recompute_once_not_double(tmp_path: Path):
    """Void both transfer legs → second void is idempotent; schedule honest once."""
    s = _session(tmp_path)
    original = date(2026, 7, 15)
    _, cash, card, item = _card_payment_setup(s, next_date=original)

    out = mark_schedule_paid(s, item.id, as_of=original, create_transaction=True)
    s.commit()
    cash_txn_id = out["transaction_id"]
    card_txn_id = out["card_transaction_id"]

    v1 = void_transaction(s, cash_txn_id, reason="undo cash")
    # Pair already voided with cash — second call must not re-reverse books
    v2 = void_transaction(s, card_txn_id, reason="undo card")
    s.commit()

    assert v1.get("paired_voided") is True
    assert v2.get("already_void") is True

    s.refresh(item)
    s.refresh(cash)
    s.refresh(card)
    assert Decimal(str(cash.current_balance)) == Decimal("1000.00")
    assert Decimal(str(card.current_balance)) == Decimal("400.00")
    cash_txn = s.get(Transaction, cash_txn_id)
    card_txn = s.get(Transaction, card_txn_id)
    assert cash_txn is not None and cash_txn.status == "void"
    assert card_txn is not None and card_txn.status == "void"

    honest = recompute_card_payment_schedule(s, card.id, as_of=date(2026, 8, 11))
    s.flush()
    honest_due = _as_date(honest.get("next_due"))

    s.refresh(item)
    if not item.active:
        assert honest.get("schedule", {}).get("ended") or (honest.get("next_payment") or 0) == 0
    else:
        assert honest_due is not None
        assert item.next_date == honest_due, (
            f"expected next_date={honest_due}, got {item.next_date}"
        )
        honest_amt = Decimal(str(honest.get("next_payment") or 0))
        if honest_amt > 0:
            assert abs(Decimal(str(item.amount))) == honest_amt
        # Not double-advanced past original + 1 month from paid date chain
        ceiling = original + relativedelta(months=2)
        assert item.next_date <= ceiling + relativedelta(months=1)
    s.close()
