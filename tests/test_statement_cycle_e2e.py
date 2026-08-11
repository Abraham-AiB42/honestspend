"""Integration dogfood: card-first statement pay lands in Safe to spend (IFPP)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.account_balance import apply_amount_to_account
from financial_os.services.autopay import recompute_card_payment_schedule
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.promo_installments import create_promo_line


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


def test_card_first_statement_pay_affects_safe(tmp_path: Path, monkeypatch):
    """Card charge → cash Card payment schedule → Safe to spend; promo carves out.

    Scenario:
    - checking 5000, buffer 0
    - card policy statement, funding checking, due in ~5 days, balance 0
    - charge card 800 → next_payment 800, cash schedule -800 on due date
    - run_ifpp → cash_spendable reflects that outflow within horizon
    - add promo line remaining 300 monthly 50 with balance 800 → payment = 550
    """
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.get(AppSettings, 1).safety_buffer = Decimal("0")

    as_of = date.today()
    due_in_days = 5
    due_target = as_of + timedelta(days=due_in_days)
    due_day = due_target.day

    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000.00"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("0.00"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("5000"),
        payment_due_day=due_day,
        statement_close_day=1,
        autopay_policy="statement",
        payment_funding_account_id=cash.id,
    )
    s.add(card)
    s.flush()

    # Zero balance → no cash payment yet
    out0 = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out0["ok"] is True
    assert Decimal(str(out0["next_payment"])) == Decimal("0.00")
    assert _card_payment_schedule(s, card.id) is None

    # Charge card 800 (ledger amount negative → credit owed increases)
    apply_amount_to_account(card, Decimal("-800.00"))
    s.flush()
    assert card.current_balance == Decimal("800.00")

    # Hook may recompute; force as_of for stable due-day math
    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out["ok"] is True
    assert Decimal(str(out["next_payment"])) == Decimal("800.00")
    assert card.next_payment_amount_cached == Decimal("800.00")
    assert card.statement_balance_cached == Decimal("800.00")

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.amount == Decimal("-800.00")
    assert sched.next_date == out["next_due"]
    # Due within ~5 days (same calendar day as due_day on/after as_of)
    assert sched.next_date is not None
    assert abs((sched.next_date - as_of).days - due_in_days) <= 1
    assert "auto=statement_cycle" in (sched.notes or "")

    # IFPP / Safe to spend must count cash card payment within horizon
    # (monthly cadence may project 1–2 dues in default horizon — compare vs off)
    sched.active = False
    s.flush()
    r_no = run_ifpp(s, profile_id=p.id)
    assert r_no.cash_spendable == Decimal("5000.00")

    sched.active = True
    s.flush()
    r = run_ifpp(s, profile_id=p.id)
    assert r.details.get("skipped_card_autopay_schedules", 0) == 0
    # At least one full payment reserved from Safe
    assert r.cash_spendable <= r_no.cash_spendable - Decimal("800.00")
    assert r.cash_spendable < r_no.cash_spendable

    # Promo: remaining 300 monthly 50 → statement pay = 800 - 300 + 50 = 550
    create_promo_line(
        s,
        card.id,
        name="ISB promo",
        principal_remaining=Decimal("300.00"),
        monthly_payment=Decimal("50.00"),
        start_date=as_of - timedelta(days=30),
        source="isb",
    )
    s.flush()
    out_promo = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert Decimal(str(out_promo["next_payment"])) == Decimal("550.00")
    assert card.next_payment_amount_cached == Decimal("550.00")
    # statement_balance carves full promo principal: 800 - 300 = 500
    assert card.statement_balance_cached == Decimal("500.00")

    sched2 = _card_payment_schedule(s, card.id)
    assert sched2 is not None
    assert sched2.amount == Decimal("-550.00")
    assert sched2.next_date == out_promo["next_due"]

    r2 = run_ifpp(s, profile_id=p.id)
    # Promo carve-out lowers cash reserve vs full-statement 800
    assert r2.cash_spendable > r.cash_spendable
    assert r2.cash_spendable <= r_no.cash_spendable - Decimal("550.00")
    # Delta vs full statement ≈ (800-550) per projected occurrence
    assert r2.cash_spendable - r.cash_spendable >= Decimal("250.00")

    s.close()
