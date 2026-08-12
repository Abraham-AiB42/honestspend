"""Rewards category card picker."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.rewards_pick import (
    pick_card_for_category,
    rate_for_category,
    set_rewards_rates,
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


def test_pick_highest_rate_for_gas(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    gas = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Gas card",
        current_balance=Decimal("100"),
        credit_limit=Decimal("2000"),
        available_credit=Decimal("1900"),
    )
    travel = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Travel",
        current_balance=Decimal("100"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4900"),
    )
    s.add_all([gas, travel])
    s.flush()
    set_rewards_rates(s, gas.id, {"gas": 5, "general": 1})
    set_rewards_rates(s, travel.id, {"gas": 1, "travel": 3, "general": 1})
    s.commit()

    assert rate_for_category(gas, "gas") == Decimal("5")
    pick = pick_card_for_category(s, category="gas", amount=Decimal("40"), profile_id=p.id)
    assert pick["best"]["name"] == "Gas card"
    assert pick["best"]["rate_percent"] == "5.00"
    assert pick["best"]["fits_amount"] is True
