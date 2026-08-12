"""Plaid special/0% APR buckets → card_intro upsert (never purchase_plan)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import (
    Account,
    Profile,
    PromoConflict,
    PromoInstallmentLine,
    init_db,
)
from honestspend.seed import seed_all
from honestspend.services.plaid_service import apply_plaid_promo_aprs
from honestspend.services.promo_upsert import upsert_promo_term


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


def _credit(s, *, nickname: str = "Plaid Card", balance: str = "2500") -> Account:
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname=nickname,
        current_balance=Decimal(balance),
        credit_limit=Decimal("10000"),
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    return card


# Plaid liabilities credit.aprs sample shape (see Plaid docs)
SPECIAL_ZERO_APRS = [
    {
        "apr_percentage": 15.24,
        "apr_type": "balance_transfer_apr",
        "balance_subject_to_apr": 1562.32,
        "interest_charge_amount": 130.22,
    },
    {
        "apr_percentage": 27.95,
        "apr_type": "cash_apr",
        "balance_subject_to_apr": 56.22,
        "interest_charge_amount": 14.81,
    },
    {
        "apr_percentage": 12.5,
        "apr_type": "purchase_apr",
        "balance_subject_to_apr": 157.01,
        "interest_charge_amount": 25.66,
    },
    {
        "apr_percentage": 0,
        "apr_type": "special",
        "balance_subject_to_apr": 1000,
        "interest_charge_amount": 0,
    },
]


def test_special_zero_apr_upserts_card_intro_not_purchase_plan(
    tmp_path: Path, monkeypatch
):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)

    out = apply_plaid_promo_aprs(s, card.id, SPECIAL_ZERO_APRS)
    s.commit()

    assert out.get("created", 0) >= 1
    lines = (
        s.query(PromoInstallmentLine)
        .filter(PromoInstallmentLine.account_id == card.id)
        .all()
    )
    assert len(lines) == 1
    line = lines[0]
    assert line.kind == "card_intro"
    assert line.source == "plaid"
    assert line.principal_remaining == Decimal("1000.00")
    assert (line.apr is None) or line.apr == Decimal("0") or line.apr == Decimal("0.0")
    # Never invent Amazon / purchase_plan rows from Plaid APR buckets
    assert not any(ln.kind == "purchase_plan" for ln in lines)
    s.close()


def test_user_card_intro_conflict_not_overwritten(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)

    user = upsert_promo_term(
        s,
        account_id=card.id,
        kind="card_intro",
        name="Intro 0%",
        principal_remaining=Decimal("2100.00"),
        monthly_payment=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=None,
        source="user",
        apr=Decimal("0"),
    )
    s.flush()
    user_remaining = user["line"].principal_remaining
    line_id = user["line"].id

    out = apply_plaid_promo_aprs(s, card.id, SPECIAL_ZERO_APRS)
    s.commit()

    line = s.get(PromoInstallmentLine, line_id)
    assert line is not None
    assert line.principal_remaining == user_remaining
    assert line.principal_remaining == Decimal("2100.00")
    assert line.source == "user"
    assert out.get("conflicts") or s.query(PromoConflict).filter(
        PromoConflict.resolved_at.is_(None)
    ).count() == 1
    assert (
        s.query(PromoConflict).filter(PromoConflict.resolved_at.is_(None)).count() == 1
    )
    s.close()


def test_nonzero_purchase_apr_alone_does_not_create_promo(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    aprs = [
        {
            "apr_percentage": 22.99,
            "apr_type": "purchase_apr",
            "balance_subject_to_apr": 500,
            "interest_charge_amount": 10,
        }
    ]
    out = apply_plaid_promo_aprs(s, card.id, aprs)
    s.commit()
    assert out.get("created", 0) == 0
    assert (
        s.query(PromoInstallmentLine)
        .filter(PromoInstallmentLine.account_id == card.id)
        .count()
        == 0
    )
    s.close()
