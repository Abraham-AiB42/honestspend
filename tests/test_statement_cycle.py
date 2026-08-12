"""Cycle windows, projected statement, and next payment math."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.statement_cycle import (
    compute_next_payment,
    next_close_date,
    previous_close_date,
    project_card_payment,
)


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_close_and_due_windows():
    # close_day=18, as_of=2026-09-15 → last close 2026-08-18, next close 2026-09-18
    as_of = date(2026, 9, 15)
    assert previous_close_date(as_of, 18) == date(2026, 8, 18)
    assert next_close_date(as_of, 18) == date(2026, 9, 18)

    # On close day: last is today, next is next month
    on_close = date(2026, 9, 18)
    assert previous_close_date(on_close, 18) == date(2026, 9, 18)
    assert next_close_date(on_close, 18) == date(2026, 10, 18)

    # After close day
    after = date(2026, 9, 20)
    assert previous_close_date(after, 18) == date(2026, 9, 18)
    assert next_close_date(after, 18) == date(2026, 10, 18)


def test_close_day_clamps_to_month_end():
    # Feb 2026 has 28 days; close_day 31 → Feb 28
    as_of = date(2026, 2, 15)
    assert previous_close_date(as_of, 31) == date(2026, 1, 31)
    assert next_close_date(as_of, 31) == date(2026, 2, 28)

    as_of_mar = date(2026, 3, 1)
    assert previous_close_date(as_of_mar, 31) == date(2026, 2, 28)
    assert next_close_date(as_of_mar, 31) == date(2026, 3, 31)


def test_statement_payment_carves_out_promo():
    # balance 5000, promo_remaining 2000, promo_due 200 → payment 3200
    assert compute_next_payment(
        policy="statement",
        current_balance=Decimal("5000"),
        min_payment=None,
        fixed_amount=None,
        promo_remaining=Decimal("2000"),
        promo_due=Decimal("200"),
    ) == Decimal("3200.00")


def test_compute_next_payment_policies():
    bal = Decimal("1000.00")

    assert compute_next_payment(
        policy="statement",
        current_balance=bal,
        min_payment=None,
        fixed_amount=None,
    ) == Decimal("1000.00")

    assert compute_next_payment(
        policy="min",
        current_balance=bal,
        min_payment=Decimal("35.00"),
        fixed_amount=None,
    ) == Decimal("35.00")

    # No min_payment → max(25, 2% of balance)
    assert compute_next_payment(
        policy="min",
        current_balance=Decimal("100.00"),
        min_payment=None,
        fixed_amount=None,
    ) == Decimal("25.00")

    assert compute_next_payment(
        policy="min",
        current_balance=Decimal("5000.00"),
        min_payment=None,
        fixed_amount=None,
    ) == Decimal("100.00")  # 2% of 5000

    assert compute_next_payment(
        policy="fixed",
        current_balance=bal,
        min_payment=None,
        fixed_amount=Decimal("150.00"),
    ) == Decimal("150.00")

    assert compute_next_payment(
        policy="promo_sink",
        current_balance=bal,
        min_payment=None,
        fixed_amount=None,
        promo_due=Decimal("75.50"),
    ) == Decimal("75.50")

    assert compute_next_payment(
        policy="none",
        current_balance=bal,
        min_payment=None,
        fixed_amount=None,
    ) == Decimal("0.00")

    assert compute_next_payment(
        policy="statement",
        current_balance=Decimal("100"),
        min_payment=None,
        fixed_amount=None,
        promo_remaining=Decimal("500"),
        promo_due=Decimal("0"),
    ) == Decimal("0.00")  # max(0, 100-500) + 0


def test_project_card_payment(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("5000.00"),
        credit_limit=Decimal("10000"),
        statement_close_day=18,
        payment_due_day=25,
        autopay_policy="statement",
        min_payment=Decimal("50"),
    )
    s.add_all([cash, card])
    s.flush()
    card.payment_funding_account_id = cash.id
    s.commit()

    as_of = date(2026, 9, 15)
    proj = project_card_payment(s, card.id, as_of=as_of)

    assert proj["last_close"] == date(2026, 8, 18)
    assert proj["next_close"] == date(2026, 9, 18)
    assert proj["next_due"] == date(2026, 9, 25)
    assert proj["funding_account_id"] == cash.id
    assert proj["policy"] == "statement"
    # v1: promo lines stub 0 → statement_balance = max(0, balance)
    assert proj["statement_balance"] == Decimal("5000.00")
    assert proj["next_payment"] == Decimal("5000.00")

    s.close()


def test_project_card_payment_min_policy(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="MC",
        current_balance=Decimal("800.00"),
        statement_close_day=1,
        payment_due_day=15,
        autopay_policy="min",
        min_payment=Decimal("40.00"),
    )
    s.add(card)
    s.commit()

    proj = project_card_payment(s, card.id, as_of=date(2026, 9, 10))
    assert proj["policy"] == "min"
    assert proj["next_payment"] == Decimal("40.00")
    assert proj["statement_balance"] == Decimal("800.00")
    assert proj["next_due"] == date(2026, 9, 15)
    s.close()
