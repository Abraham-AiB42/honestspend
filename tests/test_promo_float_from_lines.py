"""Open promo installment lines feed IFPP promo_balance / float honesty."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.promo_installments import (
    create_promo_line,
    effective_promo_balance,
    open_promo_totals,
)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_effective_promo_balance_prefers_open_lines(tmp_path: Path):
    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Promo Card",
        current_balance=Decimal("2500"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("2500"),
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=400),
        promo_balance=Decimal("999"),  # stale manual value
        payment_due_day=15,
        statement_close_day=1,
        is_cash_for_ifpp=False,
    )
    s.add(card)
    s.flush()
    create_promo_line(
        s,
        card.id,
        name="Fridge 0%",
        principal_remaining=Decimal("1800"),
        monthly_payment=Decimal("75"),
        start_date=as_of - timedelta(days=30),
        end_date=as_of + timedelta(days=400),
    )
    s.commit()

    principal, monthly = open_promo_totals(s, card.id, as_of=as_of)
    assert principal == Decimal("1800.00")
    assert monthly == Decimal("75.00")

    eff = effective_promo_balance(s, card, as_of=as_of)
    assert eff == Decimal("1800.00")

    s.refresh(card)
    # create_promo_line syncs account.promo_balance from lines
    assert Decimal(str(card.promo_balance)) == Decimal("1800.00")
    s.close()


def test_ifpp_float_uses_line_principal(tmp_path: Path, monkeypatch):
    """Card float reserves open promo line principal, not stale account field alone."""
    from financial_os.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)

    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 4, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Store Card",
        current_balance=Decimal("1500"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("3500"),
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=300),
        promo_balance=None,  # only lines
        payment_due_day=15,
        statement_close_day=1,
        is_cash_for_ifpp=False,
    )
    s.add_all([cash, card])
    s.flush()
    create_promo_line(
        s,
        card.id,
        name="Sofa",
        principal_remaining=Decimal("1500"),
        monthly_payment=Decimal("100"),
        start_date=as_of - timedelta(days=10),
    )
    # Zero buffer so cash_spendable ≈ 3000
    from financial_os.db import AppSettings

    st = s.get(AppSettings, 1)
    st.safety_buffer = Decimal("0")
    st.tax_vault_enabled = False
    s.commit()

    result = run_ifpp(s, as_of=as_of, profile_id=p.id, mode="expected")
    # $3000 cash - $1500 promo owed = $1500 residual charge capacity (also limited by available)
    plans = {c.account_id: c for c in result.cards}
    plan = plans[card.id]
    assert plan.safe_to_charge == Decimal("1500.00")
    s.close()


def test_promo_balance_clears_when_lines_paid_off(tmp_path: Path):
    """After installment principal hits 0, float must not keep stale promo_balance."""
    from financial_os.services.promo_installments import (
        sync_account_promo_balance_from_lines,
        update_promo_line,
    )

    s = _session(tmp_path)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 5, 1)
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Promo Card",
        current_balance=Decimal("100"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4900"),
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=100),
        promo_balance=Decimal("500"),
        payment_due_day=15,
        statement_close_day=1,
        is_cash_for_ifpp=False,
    )
    s.add(card)
    s.flush()
    line = create_promo_line(
        s,
        card.id,
        name="TV",
        principal_remaining=Decimal("50"),
        monthly_payment=Decimal("50"),
        start_date=as_of - timedelta(days=5),
    )
    s.commit()
    s.refresh(card)
    assert Decimal(str(card.promo_balance)) == Decimal("50.00")

    # Pay off remaining principal
    update_promo_line(s, line.id, principal_remaining=Decimal("0"))
    s.commit()
    s.refresh(card)
    assert Decimal(str(card.promo_balance)) == Decimal("0.00")
    assert effective_promo_balance(s, card, as_of=as_of) == Decimal("0")

    # Even if someone re-sets a stale balloon, lines-own rule wins while lines exist
    card.promo_balance = Decimal("999")
    s.commit()
    assert effective_promo_balance(s, card, as_of=as_of) == Decimal("0")
    sync_account_promo_balance_from_lines(s, card.id, as_of=as_of)
    s.refresh(card)
    assert Decimal(str(card.promo_balance)) == Decimal("0.00")

    # Death clock / Home promo nags must not keep a paid-off installment balloon
    from financial_os.services.promo_clock import promo_death_clock

    clock = promo_death_clock(s, as_of=as_of)
    assert all(i["account_id"] != card.id for i in clock["items"])
    s.close()
