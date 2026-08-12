from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.api.app import ScheduledIn
from honestspend.db import Account, AppSettings, Base, Profile, ScheduledItem, init_db
from honestspend.engine.schedule_expand import expand_scheduled, next_occurrence
from honestspend.seed import seed_all
from honestspend.services.ifpp_service import run_ifpp


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_expense_requires_account():
    with pytest.raises(ValidationError):
        ScheduledIn(
            profile_id=1,
            name="Netflix",
            amount=Decimal("15"),
            next_date=date(2026, 8, 1),
            kind="expense",
            account_id=None,
        )


def test_expense_amount_normalized_negative():
    body = ScheduledIn(
        profile_id=1,
        name="Netflix",
        amount=Decimal("15.99"),
        next_date=date(2026, 8, 1),
        kind="expense",
        account_id=3,
    )
    assert body.amount == Decimal("-15.99")


def test_income_amount_normalized_positive():
    body = ScheduledIn(
        profile_id=1,
        name="Paycheck",
        amount=Decimal("-2000"),
        next_date=date(2026, 8, 1),
        kind="income",
        account_id=None,
    )
    assert body.amount == Decimal("2000")


def test_expand_monthly_within_horizon():
    as_of = date(2026, 8, 5)
    occ = expand_scheduled(
        item_id=1,
        name="Rent",
        amount=Decimal("-1100"),
        next_date=date(2026, 8, 1),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=as_of + timedelta(days=90),
    )
    # Aug already passed day 1 relative to as_of 8/5 — rolls to Sep, Oct, Nov roughly
    assert len(occ) >= 2
    assert all(o.amount == Decimal("-1100") for o in occ)
    assert all(o.on_date >= as_of for o in occ)


def test_expand_respects_end_date():
    as_of = date(2026, 8, 1)
    occ = expand_scheduled(
        item_id=1,
        name="Promo sub",
        amount=Decimal("-10"),
        next_date=date(2026, 8, 1),
        cadence="monthly",
        certainty="fixed",
        as_of=as_of,
        horizon_end=as_of + timedelta(days=365),
        end_date=date(2026, 9, 15),
    )
    assert len(occ) == 2  # Aug 1 and Sep 1
    assert occ[-1].on_date <= date(2026, 9, 15)


def test_expand_inactive_empty():
    occ = expand_scheduled(
        item_id=1,
        name="X",
        amount=Decimal("-1"),
        next_date=date(2026, 8, 1),
        cadence="monthly",
        certainty="fixed",
        as_of=date(2026, 8, 1),
        horizon_end=date(2026, 12, 1),
        active=False,
    )
    assert occ == []


def test_next_occurrence_monthly():
    assert next_occurrence(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_end_scheduled_stops_ifpp(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("0")
    s.flush()
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Primary checking",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    item = ScheduledItem(
        profile_id=personal.id,
        account_id=acct.id,
        name="Huge bill",
        amount=Decimal("-4000"),
        next_date=date.today() + timedelta(days=3),
        cadence="monthly",
        certainty="fixed",
        kind="expense",
        active=True,
    )
    s.add(item)
    s.flush()

    before = run_ifpp(s, mode="conservative")
    assert before.cash_spendable < Decimal("5000")

    item.active = False
    item.end_date = date.today()
    s.flush()
    after = run_ifpp(s, mode="conservative")
    assert after.cash_spendable == Decimal("5000.00")
    s.close()
