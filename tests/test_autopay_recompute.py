"""Live card payment schedule: recompute from statement projection after charges."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.account_balance import apply_amount_to_account
from financial_os.services.autopay import (
    after_account_balance_changed,
    recompute_all_card_payments,
    recompute_card_payment_schedule,
    set_autopay,
)
from financial_os.services.ifpp_service import run_ifpp


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


def _card_and_cash(s, *, bal: Decimal = Decimal("100"), policy: str = "statement"):
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=bal,
        credit_limit=Decimal("5000"),
        available_credit=Decimal("5000") - bal,
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy=policy,
        payment_funding_account_id=cash.id,
    )
    s.add(card)
    s.flush()
    return p, cash, card


def _card_payment_schedule(s, card_id: int) -> ScheduledItem | None:
    marker = f"card_account_id={card_id};"
    return (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.notes.like(f"%{marker}%"),
        )
        .first()
    )


def test_charge_updates_next_payment_amount(tmp_path: Path, monkeypatch):
    """card policy=statement, funding=checking, balance 100 → schedule -100;
    post charge +50 owed → schedule -150, next_date still due day.
    """
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("100.00"), policy="statement")
    # as_of = today so apply_amount recompute hook (uses date.today) matches
    as_of = date.today()

    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out["ok"] is True
    assert Decimal(str(out["next_payment"])) == Decimal("100.00")
    assert card.next_payment_amount_cached == Decimal("100.00")
    assert card.statement_balance_cached == Decimal("100.00")
    assert card.next_payment_date_cached == out["next_due"]

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.name == "Card payment · Visa"
    assert sched.amount == Decimal("-100.00")
    assert sched.next_date == out["next_due"]
    assert "auto=statement_cycle" in (sched.notes or "")
    assert "policy=statement" in (sched.notes or "")
    due = sched.next_date
    assert due.day == card.payment_due_day

    # Charge: ledger amount negative → credit owed increases by 50
    apply_amount_to_account(card, Decimal("-50.00"))
    s.flush()
    assert card.current_balance == Decimal("150.00")

    # Hook via object_session should have recomputed
    sched2 = _card_payment_schedule(s, card.id)
    assert sched2 is not None
    assert sched2.id == sched.id
    assert sched2.amount == Decimal("-150.00")
    assert sched2.next_date == due
    assert sched2.next_date.day == card.payment_due_day
    assert card.next_payment_amount_cached == Decimal("150.00")
    s.close()


def test_set_autopay_creates_cash_funded_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("250.00"), policy="none")
    as_of = date(2026, 8, 5)

    r = set_autopay(
        s, account_id=card.id, policy="statement", apply_schedule=True, as_of=as_of
    )
    s.commit()
    assert r["ok"] is True
    assert r["policy"] == "statement"
    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.amount == Decimal("-250.00")
    assert "Card payment ·" in sched.name
    # Legacy card-side Autopay · rows should not be active
    legacy = (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == card.id,
            ScheduledItem.name.like("Autopay ·%"),
        )
        .count()
    )
    assert legacy == 0
    s.close()


def test_policy_none_ends_card_payment_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _p, _cash, card = _card_and_cash(s, bal=Decimal("80.00"))
    recompute_card_payment_schedule(s, card.id, as_of=date(2026, 9, 1))
    s.commit()
    assert _card_payment_schedule(s, card.id) is not None

    set_autopay(s, account_id=card.id, policy="none", apply_schedule=True)
    s.commit()
    assert _card_payment_schedule(s, card.id) is None
    s.close()


def test_cash_card_payment_counts_in_ifpp(tmp_path: Path, monkeypatch):
    """Cash-funded Card payment schedule must hit IFPP runway (not skipped)."""
    s = _session(tmp_path, monkeypatch)
    p, cash, card = _card_and_cash(s, bal=Decimal("300.00"), policy="statement")
    s.get(AppSettings, 1).safety_buffer = Decimal("0")
    recompute_card_payment_schedule(s, card.id, as_of=date.today())
    s.commit()

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id

    r = run_ifpp(s, profile_id=p.id)
    # Must not treat cash Card payment as a credit-account schedule
    assert r.details.get("skipped_card_autopay_schedules", 0) == 0
    # Safe to spend reduced by the cash payment (~300)
    assert r.cash_spendable <= Decimal("2000") - Decimal("250")
    s.close()


def test_recompute_all_card_payments(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p, _cash, card = _card_and_cash(s, bal=Decimal("40.00"))
    n = recompute_all_card_payments(s, profile_id=p.id)
    s.commit()
    assert n >= 1
    assert card.next_payment_amount_cached == Decimal("40.00")
    s.close()


def test_after_account_balance_changed_noop_for_checking(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    out = after_account_balance_changed(s, cash.id)
    assert out.get("ok") is False or out.get("skipped") is True
    s.close()
