"""Mark scheduled expense paid: advance cadence, optional txn, books move."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import (
    Account,
    Profile,
    ScheduledItem,
    Transaction,
    init_db,
    make_engine,
    make_session_factory,
)
from financial_os.seed import seed_all
from financial_os.services.schedule_mark_paid import mark_schedule_paid


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def _expense_setup(s, *, next_date: date, amount: Decimal = Decimal("-49.99"), end_date=None):
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Mark-paid cash",
        current_balance=Decimal("1000.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Streaming",
        amount=amount,
        next_date=next_date,
        end_date=end_date,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(item)
    s.flush()
    return p, cash, item


def test_monthly_bill_advances(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 3, 15))
    bal_before = Decimal(str(cash.current_balance))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 3, 15))
    s.commit()

    assert out["ok"] is True
    assert out["ended"] is False
    assert out["next_date"] == "2026-04-15"
    assert out.get("transaction_id") is not None

    s.refresh(item)
    s.refresh(cash)
    assert item.next_date == date(2026, 4, 15)
    assert item.active is True

    txn = s.get(Transaction, out["transaction_id"])
    assert txn is not None
    assert txn.status == "cleared"
    assert txn.amount == Decimal("-49.99")
    assert txn.account_id == cash.id
    assert txn.txn_date == date(2026, 3, 15)
    assert Decimal(str(cash.current_balance)) == bal_before - Decimal("49.99")
    s.close()


def test_creates_txn_and_books_move(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 8, 1), amount=Decimal("-120.00"))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 8, 1), create_transaction=True)
    s.commit()

    assert out["transaction_id"]
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("880.00")
    txns = s.query(Transaction).filter(Transaction.account_id == cash.id).all()
    assert len(txns) == 1
    assert txns[0].payee == "Streaming"
    s.close()


def test_skip_transaction(tmp_path: Path):
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 5, 10))
    bal_before = Decimal(str(cash.current_balance))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 5, 10), create_transaction=False)
    s.commit()

    assert "transaction_id" not in out
    s.refresh(item)
    s.refresh(cash)
    assert item.next_date == date(2026, 6, 10)
    assert Decimal(str(cash.current_balance)) == bal_before
    assert s.query(Transaction).count() == 0
    s.close()


def test_overdue_advances_from_as_of(tmp_path: Path):
    s = _session(tmp_path)
    _, _, item = _expense_setup(s, next_date=date(2026, 1, 5))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 3, 20), create_transaction=False)
    s.commit()

    # base becomes as_of (next < as_of), then next monthly → 2026-04-20
    assert out["next_date"] == "2026-04-20"
    s.close()


def test_paid_through_end(tmp_path: Path):
    s = _session(tmp_path)
    _, _, item = _expense_setup(
        s,
        next_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 9, 1), create_transaction=False)
    s.commit()

    assert out["ended"] is True
    s.refresh(item)
    assert item.active is False
    assert item.ended_reason == "paid through end"
    assert item.next_date == date(2026, 10, 1)
    s.close()


def test_income_rejected(tmp_path: Path):
    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    item = ScheduledItem(
        profile_id=p.id,
        name="Paycheck",
        amount=Decimal("2000"),
        next_date=date(2026, 4, 1),
        cadence="biweekly",
        certainty="fixed",
        kind="income",
        active=True,
    )
    s.add(item)
    s.flush()

    with pytest.raises(ValueError, match="expense"):
        mark_schedule_paid(s, item.id)
    s.close()


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def test_mark_paid_never_neg_with_session(tmp_path: Path, monkeypatch):
    """Mark paid passes session so warn/hard never-neg can fire on checking."""
    from financial_os.db import AppSettings
    from financial_os.services.never_neg import WouldGoNegative

    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.never_negative_enforcement = "hard"
    s.commit()

    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Thin checking",
        current_balance=Decimal("20.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Rent",
        amount=Decimal("-100.00"),
        next_date=date(2026, 4, 1),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(item)
    s.flush()

    with pytest.raises(WouldGoNegative):
        mark_schedule_paid(s, item.id, as_of=date(2026, 4, 1), create_transaction=True)
    s.close()


def test_mark_paid_matches_existing_import(tmp_path: Path):
    """When bank already posted the bill, do not double-post cash."""
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 6, 15), amount=Decimal("-49.99"))
    # Imported txn already paid this
    existing = Transaction(
        profile_id=item.profile_id,
        account_id=cash.id,
        txn_date=date(2026, 6, 14),
        amount=Decimal("-49.99"),
        payee="Streaming",
        memo="import",
        status="cleared",
        is_transfer=False,
    )
    s.add(existing)
    apply_from = Decimal(str(cash.current_balance)) - Decimal("49.99")
    cash.current_balance = apply_from
    s.commit()
    bal_before = Decimal(str(cash.current_balance))
    txn_count = s.query(Transaction).count()

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 6, 15), create_transaction=True)
    s.commit()

    assert out["matched"] is True
    assert out["transaction_id"] == existing.id
    assert s.query(Transaction).count() == txn_count  # no new cash row
    s.refresh(cash)
    s.refresh(item)
    assert Decimal(str(cash.current_balance)) == bal_before
    assert item.next_date == date(2026, 7, 15)
    s.close()


def test_mark_paid_card_payment_posts_both_sides(tmp_path: Path):
    """Card payment · schedule: cash out + credit payment + transfer link."""
    s = _session(tmp_path)
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
        current_balance=Decimal("400.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1600"),
        is_cash_for_ifpp=False,
        autopay_policy="statement",
        payment_funding_account_id=None,  # set after flush
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=Decimal("-200.00"),
        next_date=date(2026, 7, 15),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(item)
    s.commit()

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 7, 15), create_transaction=True)
    s.commit()

    assert out["ok"] is True
    assert out.get("transaction_id")
    assert out.get("card_transaction_id")
    s.refresh(cash)
    s.refresh(card)
    assert Decimal(str(cash.current_balance)) == Decimal("800.00")
    assert Decimal(str(card.current_balance)) == Decimal("200.00")  # 400 - 200 payment

    cash_txn = s.get(Transaction, out["transaction_id"])
    card_txn = s.get(Transaction, out["card_transaction_id"])
    assert cash_txn is not None and card_txn is not None
    assert cash_txn.is_transfer is True
    assert card_txn.is_transfer is True
    assert cash_txn.transfer_pair_id == card_txn.id
    assert card_txn.amount == Decimal("200.00")
    s.close()


def test_mark_paid_card_payment_advances_next_date_once(tmp_path: Path):
    """New card payment leg must not double-advance next_date via recompute.

    Regression: apply_amount_to_account(card) → recompute_card_payment_schedule
    rewrote next_date, then mark_schedule_paid called next_occurrence again
    (e.g. 2026-07-15 → 2026-09-15). Settle must step once only.
    """
    s = _session(tmp_path)
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
        current_balance=Decimal("400.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1600"),
        is_cash_for_ifpp=False,
        autopay_policy="statement",
        payment_due_day=15,
        statement_close_day=1,
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id
    original_next = date(2026, 7, 15)
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=Decimal("-200.00"),
        next_date=original_next,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(item)
    s.commit()

    out = mark_schedule_paid(s, item.id, as_of=original_next, create_transaction=True)
    s.commit()

    assert out["ok"] is True
    # Single monthly step from original next_date — not double-stepped
    assert out["next_date"] == "2026-08-15"
    s.refresh(item)
    s.refresh(cash)
    s.refresh(card)
    assert item.next_date == date(2026, 8, 15)
    assert item.active is True
    # Books still correct on both sides
    assert Decimal(str(cash.current_balance)) == Decimal("800.00")
    assert Decimal(str(card.current_balance)) == Decimal("200.00")
    assert out.get("transaction_id")
    assert out.get("card_transaction_id")
    assert out.get("card_matched") is not True  # new card leg posted
    s.close()


def test_mark_paid_card_import_only_no_double_apply(tmp_path: Path):
    """Card payment already on credit books — mark paid must not re-apply card.

    Post-settle recompute still runs on matched card legs, then restores the
    single cadence step (next_date) so amount/caches refresh without double-advance.
    """
    s = _session(tmp_path)
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
        current_balance=Decimal("200.00"),  # already paid down from 400
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1800"),
        is_cash_for_ifpp=False,
        autopay_policy="statement",
        payment_due_day=15,
        statement_close_day=1,
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id
    # Imported card payment already applied
    card_pay = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date(2026, 7, 14),
        amount=Decimal("200.00"),
        payee="Payment thank you",
        memo="import",
        status="cleared",
        is_transfer=False,
    )
    s.add(card_pay)
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=Decimal("-200.00"),
        next_date=date(2026, 7, 15),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(item)
    s.commit()
    card_bal = Decimal(str(card.current_balance))

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 7, 15), create_transaction=True)
    s.commit()

    s.refresh(card)
    s.refresh(cash)
    s.refresh(item)
    assert Decimal(str(card.current_balance)) == card_bal  # no second payment on card
    assert Decimal(str(cash.current_balance)) == Decimal("800.00")  # cash still posts
    assert out.get("card_matched") is True
    assert out.get("card_transaction_id") == card_pay.id
    # Recompute-on-match restores settled next_date (single monthly step)
    assert out["next_date"] == "2026-08-15"
    assert item.next_date == date(2026, 8, 15)
    assert item.active is True
    s.close()


def test_mark_paid_dual_import_links_only(tmp_path: Path):
    """Cash + card both imported — mark paid links transfer, no balance re-apply either side."""
    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Pay from",
        current_balance=Decimal("800.00"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1800"),
        is_cash_for_ifpp=False,
    )
    s.add_all([cash, card])
    s.flush()
    cash_txn = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 7, 14),
        amount=Decimal("-200.00"),
        payee="Card payment · Visa",
        memo="CSV:bank.csv",
        status="cleared",
    )
    card_txn = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date(2026, 7, 14),
        amount=Decimal("200.00"),
        payee="Payment thank you",
        memo="CSV:card.csv",
        status="cleared",
    )
    s.add_all([cash_txn, card_txn])
    item = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=Decimal("-200.00"),
        next_date=date(2026, 7, 15),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(item)
    s.commit()
    cash_bal = Decimal(str(cash.current_balance))
    card_bal = Decimal(str(card.current_balance))
    n = s.query(Transaction).count()

    out = mark_schedule_paid(s, item.id, as_of=date(2026, 7, 15), create_transaction=True)
    s.commit()

    assert out["matched"] is True
    assert out.get("card_matched") is True
    assert s.query(Transaction).count() == n
    s.refresh(cash)
    s.refresh(card)
    assert Decimal(str(cash.current_balance)) == cash_bal
    assert Decimal(str(card.current_balance)) == card_bal
    s.refresh(cash_txn)
    s.refresh(card_txn)
    assert cash_txn.transfer_pair_id == card_txn.id
    s.close()


def test_mark_paid_same_amount_does_not_steal(tmp_path: Path):
    """Two equal bills: matching Netflix import must not settle Spotify."""
    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("500.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    netflix = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Netflix",
        amount=Decimal("-15.99"),
        next_date=date(2026, 5, 1),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    spotify = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Spotify",
        amount=Decimal("-15.99"),
        next_date=date(2026, 5, 2),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add_all([netflix, spotify])
    s.flush()
    imported = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 5, 1),
        amount=Decimal("-15.99"),
        payee="Netflix.com",
        memo="import",
        status="cleared",
    )
    s.add(imported)
    cash.current_balance = Decimal("484.01")
    s.commit()

    out_s = mark_schedule_paid(s, spotify.id, as_of=date(2026, 5, 2), create_transaction=True)
    s.commit()
    # Spotify has no payee match → posts its own cash row
    assert out_s.get("matched") is not True
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("468.02")  # 484.01 - 15.99

    out_n = mark_schedule_paid(s, netflix.id, as_of=date(2026, 5, 1), create_transaction=True)
    s.commit()
    assert out_n["matched"] is True
    assert out_n["transaction_id"] == imported.id
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("468.02")  # no second Netflix post
    s.close()


def test_advance_schedules_after_import(tmp_path: Path):
    from financial_os.services.schedule_mark_paid import advance_schedules_after_import

    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 6, 10)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("900.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    rent = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Rent",
        amount=Decimal("-1200.00"),
        next_date=date(2026, 6, 1),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(rent)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=date(2026, 6, 1),
            amount=Decimal("-1200.00"),
            payee="Rent ACME LLC",
            memo="CSV:import.csv",
            status="cleared",
        )
    )
    s.commit()

    adv = advance_schedules_after_import(
        s, account_id=cash.id, profile_id=p.id, as_of=as_of, auto_apply=True
    )
    s.commit()
    assert adv["advanced_count"] == 1
    assert "Rent" in (adv["advanced"][0]["name"] or "")
    s.refresh(rent)
    assert rent.next_date == date(2026, 7, 1)
    s.close()


def test_advance_digit_safe_card_account_id_prefix(tmp_path: Path):
    """Filter by card account must not treat card_account_id=1 as matching =12.

    Regression: a naive LIKE '%card_account_id=1%' also matches notes for id 12
    because '1' is a digit prefix of '12'. Digit-safe filter requires ';' (or EOS).
    Same-amount cash outflows exist for both cards — only the schedule tagged for
    the filtered account may advance.
    """
    from financial_os.services.schedule_mark_paid import advance_schedules_after_import

    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 6, 10)
    next_due = date(2026, 6, 1)
    amt = Decimal("-150.00")

    # Explicit prefix-related credit ids (1 vs 12) + separate cash funding account
    card1 = Account(
        id=1,
        profile_id=p.id,
        kind="credit",
        nickname="Alpha Card",
        current_balance=Decimal("500.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1500"),
        is_cash_for_ifpp=False,
        autopay_policy="statement",
    )
    card12 = Account(
        id=12,
        profile_id=p.id,
        kind="credit",
        nickname="Beta Card",
        current_balance=Decimal("500.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1500"),
        is_cash_for_ifpp=False,
        autopay_policy="statement",
    )
    cash = Account(
        id=100,
        profile_id=p.id,
        kind="checking",
        nickname="Funding cash",
        current_balance=Decimal("2000.00"),
        is_cash_for_ifpp=True,
    )
    s.add_all([card1, card12, cash])
    s.flush()
    card1.payment_funding_account_id = cash.id
    card12.payment_funding_account_id = cash.id

    sched1 = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Alpha Card",
        amount=amt,
        next_date=next_due,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes="card_account_id=1; policy=statement; timing=on_due; auto=statement_cycle",
    )
    sched12 = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Beta Card",
        amount=amt,
        next_date=next_due,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes="card_account_id=12; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add_all([sched1, sched12])
    s.flush()

    # Same-amount cash outflows; payees distinguish Alpha vs Beta
    s.add_all(
        [
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=next_due,
                amount=amt,
                payee="Card payment · Alpha Card",
                memo="CSV:bank.csv",
                status="cleared",
            ),
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=next_due,
                amount=amt,
                payee="Card payment · Beta Card",
                memo="CSV:bank.csv",
                status="cleared",
            ),
        ]
    )
    cash.current_balance = Decimal("1700.00")
    s.commit()

    # Filter as if importing card #1 statement — must not also settle card #12
    adv1 = advance_schedules_after_import(
        s, account_id=1, profile_id=p.id, as_of=as_of, auto_apply=True
    )
    s.commit()
    assert adv1["advanced_count"] == 1
    assert adv1["advanced"][0]["scheduled_id"] == sched1.id
    assert "Alpha" in (adv1["advanced"][0]["name"] or "")
    s.refresh(sched1)
    s.refresh(sched12)
    assert sched1.next_date == date(2026, 7, 1)
    assert sched12.next_date == next_due  # untouched — digit-safe filter

    # Filter as if importing card #12 — advances only Beta (not re-touch Alpha)
    adv12 = advance_schedules_after_import(
        s, account_id=12, profile_id=p.id, as_of=as_of, auto_apply=True
    )
    s.commit()
    assert adv12["advanced_count"] == 1
    assert adv12["advanced"][0]["scheduled_id"] == sched12.id
    assert "Beta" in (adv12["advanced"][0]["name"] or "")
    s.refresh(sched1)
    s.refresh(sched12)
    assert sched1.next_date == date(2026, 7, 1)
    assert sched12.next_date == date(2026, 7, 1)
    s.close()


def test_advance_digit_safe_card_account_id_does_not_cross_match(tmp_path: Path):
    """Import filter for id=1 must ignore schedule tagged card_account_id=12 only.

    When the only cash match is for the *other* card's payment name, filtering by
    account_id=1 advances nothing (and must not steal the id=12 schedule).
    """
    from financial_os.services.schedule_mark_paid import advance_schedules_after_import

    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 6, 10)
    next_due = date(2026, 6, 1)
    amt = Decimal("-99.00")

    card1 = Account(
        id=1,
        profile_id=p.id,
        kind="credit",
        nickname="ShortId",
        current_balance=Decimal("300.00"),
        credit_limit=Decimal("1000"),
        available_credit=Decimal("700"),
        is_cash_for_ifpp=False,
    )
    card12 = Account(
        id=12,
        profile_id=p.id,
        kind="credit",
        nickname="LongerId",
        current_balance=Decimal("300.00"),
        credit_limit=Decimal("1000"),
        available_credit=Decimal("700"),
        is_cash_for_ifpp=False,
    )
    cash = Account(
        id=50,
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("500.00"),
        is_cash_for_ifpp=True,
    )
    s.add_all([card1, card12, cash])
    s.flush()

    # Only the prefix-sibling schedule exists (id=12). Broken LIKE '%card_account_id=1%'
    # would select it when filtering by account_id=1.
    sched12 = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · LongerId",
        amount=amt,
        next_date=next_due,
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes="card_account_id=12; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(sched12)
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=next_due,
            amount=amt,
            payee="Card payment · LongerId",
            memo="import",
            status="cleared",
        )
    )
    s.commit()

    adv = advance_schedules_after_import(
        s, account_id=1, profile_id=p.id, as_of=as_of, auto_apply=True
    )
    s.commit()
    assert adv["advanced_count"] == 0
    s.refresh(sched12)
    assert sched12.next_date == next_due

    # Correct card id still advances
    adv_ok = advance_schedules_after_import(
        s, account_id=12, profile_id=p.id, as_of=as_of, auto_apply=True
    )
    s.commit()
    assert adv_ok["advanced_count"] == 1
    s.refresh(sched12)
    assert sched12.next_date == date(2026, 7, 1)
    s.close()


def test_require_match_txn_id_wrong_or_missing_no_mutations(tmp_path: Path):
    """require_match_txn_id that fails: no cash created, next_date unchanged."""
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 6, 15), amount=Decimal("-49.99"))
    wrong = Transaction(
        profile_id=item.profile_id,
        account_id=cash.id,
        txn_date=date(2026, 6, 14),
        amount=Decimal("-12.00"),
        payee="Coffee Shop",
        memo="import",
        status="cleared",
    )
    s.add(wrong)
    s.commit()

    item_id = item.id
    cash_id = cash.id
    wrong_id = wrong.id
    bal_before = Decimal(str(cash.current_balance))
    next_before = item.next_date
    txn_count = s.query(Transaction).count()

    # Missing id
    with pytest.raises(ValueError, match="not found"):
        mark_schedule_paid(
            s,
            item_id,
            as_of=date(2026, 6, 15),
            create_transaction=True,
            require_match_txn_id=999_999,
        )
    s.rollback()
    item = s.get(ScheduledItem, item_id)
    cash = s.get(Account, cash_id)
    assert item.next_date == next_before
    assert Decimal(str(cash.current_balance)) == bal_before
    assert s.query(Transaction).count() == txn_count

    # Wrong amount on same account
    with pytest.raises(ValueError, match="amount does not match"):
        mark_schedule_paid(
            s,
            item_id,
            as_of=date(2026, 6, 15),
            create_transaction=True,
            require_match_txn_id=wrong_id,
        )
    s.rollback()
    item = s.get(ScheduledItem, item_id)
    cash = s.get(Account, cash_id)
    assert item.next_date == next_before
    assert Decimal(str(cash.current_balance)) == bal_before
    # No extra cash from mark_schedule_paid create path
    assert s.query(Transaction).filter(Transaction.payee == "Streaming").count() == 0
    assert s.query(Transaction).count() == txn_count
    s.close()


def test_require_match_txn_id_success_still_matches(tmp_path: Path):
    """require_match_txn_id with the right cash txn settles without creating cash."""
    s = _session(tmp_path)
    _, cash, item = _expense_setup(s, next_date=date(2026, 6, 15), amount=Decimal("-49.99"))
    existing = Transaction(
        profile_id=item.profile_id,
        account_id=cash.id,
        txn_date=date(2026, 6, 14),
        amount=Decimal("-49.99"),
        payee="Streaming",
        memo="import",
        status="cleared",
    )
    s.add(existing)
    cash.current_balance = Decimal(str(cash.current_balance)) - Decimal("49.99")
    s.commit()
    bal_before = Decimal(str(cash.current_balance))
    n = s.query(Transaction).count()

    out = mark_schedule_paid(
        s,
        item.id,
        as_of=date(2026, 6, 15),
        create_transaction=True,
        require_match_txn_id=existing.id,
    )
    s.commit()
    assert out["matched"] is True
    assert out["transaction_id"] == existing.id
    assert s.query(Transaction).count() == n
    s.refresh(item)
    s.refresh(cash)
    assert item.next_date == date(2026, 7, 15)
    assert Decimal(str(cash.current_balance)) == bal_before
    s.close()


def test_card_payment_already_paired_not_rebound(tmp_path: Path):
    """Second schedule must not re-bind a card payment already transfer-paired."""
    s = _session(tmp_path)
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
        current_balance=Decimal("200.00"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1800"),
        is_cash_for_ifpp=False,
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id

    # Card payment already paired to first cash leg
    cash_a = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 7, 14),
        amount=Decimal("-200.00"),
        payee="Card payment · Visa",
        memo="import A",
        status="cleared",
        is_transfer=True,
    )
    card_pay = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date(2026, 7, 14),
        amount=Decimal("200.00"),
        payee="Payment thank you",
        memo="import card",
        status="cleared",
        is_transfer=True,
    )
    s.add_all([cash_a, card_pay])
    s.flush()
    cash_a.transfer_pair_id = card_pay.id
    card_pay.transfer_pair_id = cash_a.id

    # Second cash outflow for another equal payment (unpaired)
    cash_b = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date(2026, 7, 15),
        amount=Decimal("-200.00"),
        payee="Card payment · Visa",
        memo="import B",
        status="cleared",
    )
    s.add(cash_b)

    sched_b = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Card payment · Visa",
        amount=Decimal("-200.00"),
        next_date=date(2026, 7, 15),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
        notes=f"card_account_id={card.id}; policy=statement; timing=on_due; auto=statement_cycle",
    )
    s.add(sched_b)
    s.commit()
    card_bal = Decimal(str(card.current_balance))
    pair_before = card_pay.transfer_pair_id

    out = mark_schedule_paid(
        s,
        sched_b.id,
        as_of=date(2026, 7, 15),
        create_transaction=True,
        require_match_txn_id=cash_b.id,
    )
    s.commit()

    s.refresh(card_pay)
    s.refresh(cash_b)
    s.refresh(card)
    # Original pair intact
    assert card_pay.transfer_pair_id == pair_before == cash_a.id
    # Second cash must not point at the already-paired card payment
    assert cash_b.transfer_pair_id != card_pay.id
    # Either new card side created, or unmatched card side — not rebinding A
    if out.get("card_transaction_id"):
        assert out["card_transaction_id"] != card_pay.id
    # Books: if new card payment posted, balance drops; if not, unchanged
    # In any case original pair's books already applied — do not double-apply that row
    assert Decimal(str(card.current_balance)) <= card_bal
    s.close()


def test_api_mark_paid(api_client: TestClient):
    import financial_os.api.app as app_mod

    with app_mod.SessionLocal() as s:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="API cash",
            current_balance=Decimal("500.00"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.flush()
        item = ScheduledItem(
            profile_id=p.id,
            account_id=cash.id,
            name="Gym",
            amount=Decimal("-30.00"),
            next_date=date(2026, 6, 1),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
        s.add(item)
        s.commit()
        sid = item.id
        cash_id = cash.id

    r = api_client.post(f"/api/scheduled/{sid}/mark-paid", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["next_date"] == "2026-07-01"
    assert body.get("transaction_id")

    r2 = api_client.post(
        f"/api/scheduled/{sid}/mark-paid",
        json={"create_transaction": False},
    )
    assert r2.status_code == 200
    assert r2.json().get("transaction_id") is None
    assert r2.json()["next_date"] == "2026-08-01"

    with app_mod.SessionLocal() as s:
        cash = s.get(Account, cash_id)
        assert Decimal(str(cash.current_balance)) == Decimal("470.00")
