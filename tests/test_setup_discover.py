"""Setup discover: cards/loans/bills from cash outflows."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, ScheduledItem, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.setup_discover import (
    apply_discoveries,
    discover_liabilities,
    set_account_payment_option,
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


def test_discover_card_and_investment(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    base = date(2026, 6, 1)
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=base + timedelta(days=30 * i),
                amount=Decimal("-220.00"),
                payee="Payment to Capital One",
            )
        )
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=base + timedelta(days=14 * i),
                amount=Decimal("-100.00"),
                payee="Vanguard Brokerage ACH",
            )
        )
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=base + timedelta(days=28 * i),
                amount=Decimal("-89.99"),
                payee="NETFLIX.COM",
            )
        )
    s.commit()

    disc = discover_liabilities(s, profile_id=p.id, as_of=date(2026, 8, 15), lookback_days=120)
    types = {x["type"] for x in disc["proposals"]}
    assert "credit" in types
    assert "recurring_investment" in types
    # netflix as bill via classify or recurring
    assert disc["count"] >= 2
    s.close()


def test_apply_creates_card_with_payment_option(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Checking",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()

    res = apply_discoveries(
        s,
        profile_id=p.id,
        accepted=[
            {
                "type": "credit",
                "name": "Capital One",
                "median_amount": "220",
                "cadence": "monthly",
                "suggested_next_date": "2026-09-01",
                "payment_option": "interest_saving",
                "selected": True,
            },
            {
                "type": "recurring_investment",
                "name": "Vanguard",
                "median_amount": "100",
                "cadence": "biweekly",
                "suggested_next_date": "2026-08-20",
                "selected": True,
            },
            {
                "type": "bill",
                "name": "Netflix",
                "median_amount": "15.99",
                "cadence": "monthly",
                "suggested_next_date": "2026-09-05",
                "selected": True,
            },
        ],
    )
    s.commit()
    assert res["count"] == 3
    card = s.query(Account).filter(Account.kind == "credit").one()
    assert card.payment_option == "interest_saving"
    assert card.autopay_policy == "promo_sink"
    # Card must NOT create a scheduled "{name} payment" (IFPP double-count)
    pay_bills = (
        s.query(ScheduledItem)
        .filter(ScheduledItem.active.is_(True), ScheduledItem.name == "Capital One payment")
        .all()
    )
    assert pay_bills == []
    bills = s.query(ScheduledItem).filter(ScheduledItem.active.is_(True)).all()
    assert len(bills) >= 2  # investment + netflix only
    invest = [b for b in bills if "invest" in (b.name or "").lower() or "Vanguard" in (b.name or "")]
    assert invest
    # Idempotent re-apply
    res2 = apply_discoveries(
        s,
        profile_id=p.id,
        accepted=[
            {
                "type": "credit",
                "name": "Capital One",
                "median_amount": "220",
                "payment_option": "interest_saving",
                "selected": True,
            }
        ],
    )
    assert res2["skipped_count"] >= 1
    assert s.query(Account).filter(Account.kind == "credit").count() == 1
    s.close()


def test_set_payment_option_fixed(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Amex",
        current_balance=Decimal("500"),
        payment_option="interest_saving",
    )
    s.add(card)
    s.commit()
    out = set_account_payment_option(
        s, card.id, payment_option="fixed", payment_fixed_amount=Decimal("150")
    )
    s.commit()
    assert out["payment_option"] == "fixed"
    assert out["autopay_policy"] == "fixed"
    s.refresh(card)
    assert card.payment_fixed_amount == Decimal("150")
    assert card.autopay_policy == "fixed"
    assert card.payment_option == "fixed"
    s.close()


def test_apply_default_credit_policy_is_statement(tmp_path: Path, monkeypatch):
    """Discover accept without payment_option → autopay_policy=statement (authority)."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Checking",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()
    res = apply_discoveries(
        s,
        profile_id=p.id,
        accepted=[
            {
                "type": "credit",
                "name": "Chase Sapphire",
                "median_amount": "100",
                "cadence": "monthly",
                "suggested_next_date": "2026-09-01",
                "selected": True,
            }
        ],
    )
    s.commit()
    assert res["count"] == 1
    card = s.query(Account).filter(Account.kind == "credit").one()
    assert card.autopay_policy == "statement"
    assert card.payment_option == "statement"  # alias mirror only
    s.close()
