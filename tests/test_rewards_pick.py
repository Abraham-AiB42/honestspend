"""Rewards category card picker — float first, then rate (via smart_charge)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.rewards_pick import (
    pick_card_for_category,
    rate_for_category,
    set_rewards_rates,
)

AS_OF = date(2026, 9, 1)


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    row = s.get(AppSettings, 1)
    if row is not None:
        row.safety_buffer = Decimal("0")
        row.budget_reserve_enabled = False
    s.commit()
    return s


def _checking(s, p, *, balance: Decimal = Decimal("5000")) -> Account:
    a = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=balance,
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(a)
    s.flush()
    return a


def _card(
    s,
    p,
    *,
    nickname: str,
    due_day: int,
    balance: Decimal = Decimal("100"),
    limit: Decimal = Decimal("5000"),
) -> Account:
    a = Account(
        profile_id=p.id,
        kind="credit",
        nickname=nickname,
        current_balance=balance,
        credit_limit=limit,
        available_credit=limit - balance,
        payment_due_day=due_day,
        statement_close_day=1,
        autopay_policy="statement",
        min_payment=Decimal("25"),
    )
    s.add(a)
    s.flush()
    return a


def test_pick_highest_rate_for_gas(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _checking(s, p)
    # Same due day so rate decides among safe cards
    gas = _card(s, p, nickname="Gas card", due_day=20, limit=Decimal("2000"))
    travel = _card(s, p, nickname="Travel", due_day=20, limit=Decimal("5000"))
    set_rewards_rates(s, gas.id, {"gas": 5, "general": 1})
    set_rewards_rates(s, travel.id, {"gas": 1, "travel": 3, "general": 1})
    s.commit()

    assert rate_for_category(gas, "gas") == Decimal("5")
    pick = pick_card_for_category(
        s,
        category="gas",
        amount=Decimal("40"),
        profile_id=p.id,
        as_of=AS_OF,
    )
    assert pick["best"]["name"] == "Gas card"
    assert pick["best"]["rate_percent"] == "5.00"
    assert pick["best"]["fits_amount"] is True
    s.close()


def test_higher_rate_sooner_due_loses_to_more_float(tmp_path, monkeypatch):
    """Order is float then rate: 5% due soon loses to 1% with longer float."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _checking(s, p, balance=Decimal("5000"))
    long_float = _card(s, p, nickname="Long float", due_day=30)  # ~29d from Sep 1
    high_rate = _card(s, p, nickname="High rate soon", due_day=11)  # ~10d
    set_rewards_rates(s, long_float.id, {"gas": 1, "general": 1})
    set_rewards_rates(s, high_rate.id, {"gas": 5, "general": 1})
    s.commit()

    pick = pick_card_for_category(
        s,
        category="gas",
        amount=Decimal("50"),
        profile_id=p.id,
        as_of=AS_OF,
    )
    assert pick["best"]["name"] == "Long float"
    assert pick["best"]["rate_percent"] == "1.00"
    names = [c["name"] for c in pick["cards"]]
    assert names[0] == "Long float"
    assert "High rate soon" in names
    assert names.index("Long float") < names.index("High rate soon")
    assert "float" in (pick.get("hint") or "").lower() or "pay date" in (
        pick.get("hint") or ""
    ).lower()
    s.close()
