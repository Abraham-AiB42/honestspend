"""Three-step budget plan: income, recurring bills, variable envelopes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.data.budget_playbook import RECURRING_KINDS, VARIABLE_ENVELOPES
from honestspend.db import Account, Category, Profile, ScheduledItem, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.setup_budget_plan import (
    apply_plan,
    bills_review,
    employer_from_payee,
    income_review,
    match_recurring_kind,
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


def test_playbook_covers_us_household_bills():
    ids = {k["id"] for k in RECURRING_KINDS}
    for need in ("mortgage", "rent", "electric", "cell", "internet", "auto_loan"):
        assert need in ids
    env = {e["id"] for e in VARIABLE_ENVELOPES}
    assert "groceries" in env and "dining" in env and "fuel" in env
    assert any(e.get("subcategories") for e in VARIABLE_ENVELOPES)
    assert match_recurring_kind("Payment to T-Mobile")["id"] == "cell"
    assert match_recurring_kind("XCEL EZ-PAY WEB")["id"] == "electric"
    assert match_recurring_kind("COMCAST")["id"] == "internet"


def test_income_uses_employer_not_payroll_processor():
    assert employer_from_payee("ADP DIRECT DEP ACME") == "ACME"
    assert employer_from_payee("GUSTO *NORTH STUDIO LLC")
    assert "GUSTO" not in (employer_from_payee("GUSTO *NORTH STUDIO LLC") or "").upper()
    assert employer_from_payee("ADP PAYROLL") is None
    assert employer_from_payee("ACME CORP DIR DEP")
    assert "ACME" in (employer_from_payee("ACME CORP DIR DEP") or "").upper()


def test_income_and_bills_from_history(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("4000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    for i in range(4):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=date.today() - timedelta(days=14 * i),
                amount=Decimal("2200"),
                payee="ACME CORP DIR DEP",
            )
        )
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=date.today() - timedelta(days=30 * i + 3),
                amount=Decimal("-130.69"),
                payee="Payment to T-Mobile",
            )
        )
    s.commit()
    inc = income_review(s, profile_id=p.id)
    assert inc["step"] == "income"
    assert inc["detected"]
    assert any("ACME" in (r["name"] or "").upper() for r in inc["detected"])
    assert not any("ADP" in (r["name"] or "").upper() for r in inc["detected"])
    applied = apply_plan(
        s,
        step="income",
        updates=[{"create": True, "selected": True, **inc["detected"][0]}],
        profile_id=p.id,
    )
    assert applied["changed"]
    assert s.query(ScheduledItem).filter(ScheduledItem.kind == "income", ScheduledItem.active.is_(True)).count() >= 1

    bills = bills_review(s, profile_id=p.id)
    assert bills["step"] == "bills"
    assert any(d.get("kind_id") == "cell" for d in bills["detected"])
    cell = next(d for d in bills["detected"] if d.get("kind_id") == "cell")
    apply_plan(s, step="bills", updates=[{**cell, "selected": True}], profile_id=p.id)
    names = [x.name for x in s.query(ScheduledItem).filter(ScheduledItem.kind == "expense", ScheduledItem.active.is_(True))]
    assert any("mobile" in (n or "").lower() or "t-mobile" in (n or "").lower() or "T-Mobile" in (n or "") for n in names)
    s.close()


def test_business_payroll_processor_is_not_income(tmp_path: Path, monkeypatch):
    from honestspend.services.profiles import create_profile

    s = _session(tmp_path, monkeypatch)
    biz = create_profile(s, display_name="Studio LLC", entity_type="business")
    cash = Account(
        profile_id=biz.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("20000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    for i in range(3):
        s.add(
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                txn_date=date.today() - timedelta(days=14 * i),
                amount=Decimal("8500"),
                payee="ADP PAYROLL",
            )
        )
    s.commit()
    inc = income_review(s, profile_id=biz.id)
    assert not any("ADP" in (r["name"] or "").upper() for r in inc["detected"])
    s.close()
