"""Reconcile books vs institution balance."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.autopay import recompute_card_payment_schedule
from financial_os.services.reconcile import reconcile_report, set_institution_balance, trust_balance


def test_reconcile_drift_and_trust(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    a = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Main",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(a)
    s.commit()

    set_institution_balance(s, a.id, Decimal("950"))
    s.commit()
    rep = reconcile_report(s)
    assert rep["drifted"] >= 1
    row = next(x for x in rep["accounts"] if x["account_id"] == a.id)
    assert row["status"] == "drift"
    assert Decimal(row["drift"]) == Decimal("50.00")

    trust_balance(s, a.id, trust="institution")
    s.commit()
    s.refresh(a)
    assert a.current_balance == Decimal("950")
    rep2 = reconcile_report(s)
    row2 = next(x for x in rep2["accounts"] if x["account_id"] == a.id)
    assert row2["status"] == "matched"
    s.close()


def test_trust_institution_updates_credit_available(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Card",
        current_balance=Decimal("200"),
        institution_balance=Decimal("150"),
        credit_limit=Decimal("1000"),
        available_credit=Decimal("800"),
    )
    s.add(card)
    s.flush()
    out = trust_balance(s, card.id, trust="institution")
    s.refresh(card)
    assert card.current_balance == Decimal("150")
    assert card.available_credit == Decimal("850")
    assert Decimal(str(out.get("books_before"))) == Decimal("200")
    s.close()


def test_trust_institution_recomputes_card_payment_schedule(tmp_path: Path):
    """When credit books change via trust=institution, cash schedule follows."""
    eng = create_engine(f"sqlite:///{(tmp_path / 'trust_sched.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    card = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        institution_balance=Decimal("350"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4800"),
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy="statement",
        payment_funding_account_id=cash.id,
    )
    s.add(card)
    s.flush()
    recompute_card_payment_schedule(s, card.id)
    s.commit()
    marker = f"card_account_id={card.id};"
    sched = (
        s.query(ScheduledItem)
        .filter(ScheduledItem.active.is_(True), ScheduledItem.notes.like(f"%{marker}%"))
        .first()
    )
    assert sched is not None
    assert sched.amount == Decimal("-200.00")

    trust_balance(s, card.id, trust="institution")
    s.commit()
    s.refresh(card)
    assert card.current_balance == Decimal("350")
    assert card.next_payment_amount_cached == Decimal("350.00")
    sched2 = (
        s.query(ScheduledItem)
        .filter(ScheduledItem.active.is_(True), ScheduledItem.notes.like(f"%{marker}%"))
        .first()
    )
    assert sched2 is not None
    assert sched2.amount == Decimal("-350.00")
    s.close()
