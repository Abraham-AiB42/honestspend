"""Period budgets: windows, actuals, reserve, suggestions, cuts."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import (
    Account,
    AppSettings,
    Base,
    BudgetRule,
    Category,
    Profile,
    Transaction,
    init_db,
)
from financial_os.services import budget_periods as bp
from financial_os.services import budget_service as bs
from financial_os.services.home_simple import build_home_simple


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    # minimal seed
    p = Profile(
        slug="personal",
        display_name="Personal",
        entity_type="personal",
        tax_form_primary="1040",
        is_default=True,
    )
    session.add(p)
    session.flush()
    session.add(AppSettings(id=1, budget_reserve_enabled=True, budget_workdays=31))
    food = Category(
        code="TEST_FOOD",
        display_name="Food",
        scope="personal",
        profile_id=p.id,
        budget_group="food",
    )
    gas = Category(
        code="TEST_GAS",
        display_name="Gas",
        scope="personal",
        profile_id=p.id,
        budget_group="transport",
    )
    session.add_all([food, gas])
    session.flush()
    acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    session.add(acct)
    session.commit()
    yield session
    session.close()


def test_week_bounds_monday_start():
    # 2026-08-12 is Wednesday
    d = date(2026, 8, 12)
    s, e = bp.week_bounds(d, week_starts_on=0)
    assert s == date(2026, 8, 10)  # Mon
    assert e == date(2026, 8, 16)  # Sun


def test_daily_window_none_on_weekend():
    sat = date(2026, 8, 15)  # Saturday
    assert bp.period_window("daily", sat, active_weekdays=31) is None
    mon = date(2026, 8, 10)
    w = bp.period_window("daily", mon, active_weekdays=31)
    assert w is not None and w.start == mon


def test_budget_status_and_reserve(db):
    p = db.query(Profile).first()
    food = db.query(Category).filter(Category.code == "TEST_FOOD").one()
    acct = db.query(Account).first()
    as_of = date(2026, 8, 10)  # Monday
    # plan $26/day food; spent $10 today
    rule = bs.create_rule(
        db,
        profile_id=p.id,
        category_id=food.id,
        period="daily",
        amount=Decimal("26"),
        name="Food",
        active_weekdays=31,
    )
    db.add(
        Transaction(
            profile_id=p.id,
            account_id=acct.id,
            category_id=food.id,
            txn_date=as_of,
            amount=Decimal("-10"),
            payee="Lunch",
        )
    )
    db.commit()
    st = bs.rule_status(db, rule, as_of=as_of)
    assert st is not None
    assert st["plan"] == "26.00"
    assert st["actual"] == "10.00"
    assert st["remaining"] == "16.00"
    assert st["status"] == "ok"
    reserve = bs.budget_reserve_total(db, profile_id=p.id, as_of=as_of)
    assert reserve == Decimal("16.00")


def test_no_carryover_across_days(db):
    p = db.query(Profile).first()
    food = db.query(Category).filter(Category.code == "TEST_FOOD").one()
    acct = db.query(Account).first()
    mon = date(2026, 8, 10)
    tue = date(2026, 8, 11)
    bs.create_rule(
        db, profile_id=p.id, category_id=food.id, period="daily", amount=Decimal("20")
    )
    # spent nothing Monday → remaining 20 Mon; Tuesday still full 20 (no carry)
    st_mon = bs.budgets_status(db, profile_id=p.id, as_of=mon)
    st_tue = bs.budgets_status(db, profile_id=p.id, as_of=tue)
    assert st_mon["items"][0]["remaining"] == "20.00"
    assert st_tue["items"][0]["remaining"] == "20.00"
    db.add(
        Transaction(
            profile_id=p.id,
            account_id=acct.id,
            category_id=food.id,
            txn_date=mon,
            amount=Decimal("-5"),
            payee="Mon",
        )
    )
    db.commit()
    st_mon2 = bs.budgets_status(db, profile_id=p.id, as_of=mon)
    st_tue2 = bs.budgets_status(db, profile_id=p.id, as_of=tue)
    assert st_mon2["items"][0]["remaining"] == "15.00"
    assert st_tue2["items"][0]["remaining"] == "20.00"


def test_suggestion_trailing_average(db):
    p = db.query(Profile).first()
    food = db.query(Category).filter(Category.code == "TEST_FOOD").one()
    acct = db.query(Account).first()
    # 5 workdays with $10 each
    start = date(2026, 8, 3)  # Monday
    for i in range(5):
        d = start + timedelta(days=i)
        db.add(
            Transaction(
                profile_id=p.id,
                account_id=acct.id,
                category_id=food.id,
                txn_date=d,
                amount=Decimal("-10"),
                payee="Food",
            )
        )
    db.commit()
    sug = bs.suggest_for_category(
        db,
        profile_id=p.id,
        category_id=food.id,
        period="daily",
        as_of=date(2026, 8, 10),
        active_weekdays=31,
    )
    # average over lookback includes zeros for other workdays in 90d
    assert Decimal(sug["suggested_amount"]) >= 0
    assert sug["stats"]["n"] > 0
    assert "avg" in sug["stats"]


def test_cut_skip_workdays(db):
    p = db.query(Profile).first()
    food = db.query(Category).filter(Category.code == "TEST_FOOD").one()
    as_of = date(2026, 8, 10)
    rule = bs.create_rule(
        db, profile_id=p.id, category_id=food.id, period="daily", amount=Decimal("30")
    )
    db.commit()
    before = bs.budget_reserve_total(db, profile_id=p.id, as_of=as_of)
    assert before == Decimal("30.00")
    bs.apply_cut(
        db,
        budget_rule_id=rule.id,
        kind="skip_workdays",
        params={"workdays": 1},
        as_of=as_of,
    )
    db.commit()
    after = bs.budget_reserve_total(db, profile_id=p.id, as_of=as_of)
    assert after == Decimal("0.00")


def test_home_safe_to_spend_subtracts_reserve(db):
    p = db.query(Profile).first()
    food = db.query(Category).filter(Category.code == "TEST_FOOD").one()
    as_of = date(2026, 8, 10)
    bs.create_rule(
        db, profile_id=p.id, category_id=food.id, period="daily", amount=Decimal("100")
    )
    db.commit()
    home = build_home_simple(db, profile_id=p.id, as_of=as_of)
    # raw cash is high after buffer; after budget reserve should be lower by 100
    before = Decimal(home["safe_to_spend_before_budgets"])
    after = Decimal(home["safe_to_spend"])
    reserve = Decimal(home["budget_reserve"])
    assert reserve == Decimal("100.00")
    assert after == before - reserve
    assert "budgets" in home


def test_suggestions_include_unbudgeted_top_spend(db):
    p = db.query(Profile).first()
    gas = db.query(Category).filter(Category.code == "TEST_GAS").one()
    acct = db.query(Account).first()
    # gas spend history, no gas budget rule
    for i in range(4):
        db.add(
            Transaction(
                profile_id=p.id,
                account_id=acct.id,
                category_id=gas.id,
                txn_date=date(2026, 8, 3) + timedelta(days=i * 7),
                amount=Decimal("-40"),
                payee="Pump",
            )
        )
    db.commit()
    sug = bs.suggestions(db, profile_id=p.id, as_of=date(2026, 8, 20))
    gas_items = [i for i in sug["items"] if i.get("category_id") == gas.id]
    assert len(gas_items) >= 1
    assert gas_items[0].get("has_rule") is False
    assert Decimal(gas_items[0]["suggested_amount"]) > 0
