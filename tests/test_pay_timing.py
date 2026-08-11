"""Pay timing (close / day before) and books policy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.autopay import recompute_card_payment_schedule
from financial_os.services.promo_installments import create_promo_line
from financial_os.services.statement_cycle import (
    compute_next_payment,
    day_before,
    next_payment_date,
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


def test_next_payment_date_on_close_and_day_before():
    as_of = date(2026, 9, 15)
    assert next_payment_date(
        as_of=as_of, timing="on_close", close_day=18, due_day=15
    ) == date(2026, 9, 18)
    assert next_payment_date(
        as_of=as_of, timing="day_before_close", close_day=18, due_day=15
    ) == date(2026, 9, 17)
    assert day_before(date(2026, 10, 1)) == date(2026, 9, 30)


def test_books_policy_full_balance_ignores_promo_carve_out():
    assert compute_next_payment(
        policy="books",
        current_balance=Decimal("800"),
        min_payment=None,
        fixed_amount=None,
        promo_remaining=Decimal("300"),
        promo_due=Decimal("50"),
    ) == Decimal("800.00")


def test_project_books_with_timing_and_warning(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("800"),
        credit_limit=Decimal("5000"),
        payment_due_day=28,
        statement_close_day=18,
        autopay_policy="books",
        payment_timing="day_before_close",
        payment_funding_account_id=cash.id,
    )
    s.add(card)
    s.flush()
    create_promo_line(
        s,
        account_id=card.id,
        name="0% gear",
        principal_remaining=Decimal("300"),
        monthly_payment=Decimal("50"),
        start_date=date(2026, 1, 1),
    )
    s.commit()

    as_of = date(2026, 9, 15)
    proj = project_card_payment(s, card.id, as_of=as_of)
    assert proj["next_payment"] == Decimal("800.00")
    assert proj["next_due"] == date(2026, 9, 17)
    assert proj["payment_timing"] == "day_before_close"
    assert proj["warnings"]
    assert any("promo" in w.lower() for w in proj["warnings"])

    r = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert r["next_payment"] == Decimal("800.00")
    assert r["next_due"] == date(2026, 9, 17)
    sched = (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.notes.like(f"%card_account_id={card.id};%"),
        )
        .one()
    )
    assert sched.amount == Decimal("-800.00")
    assert sched.next_date == date(2026, 9, 17)
    assert sched.account_id == cash.id
