"""smart_charge ranker: float first, then rewards; cash only if no card is safe."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, init_db
from honestspend.seed import seed_all
from honestspend.services.rewards_pick import set_rewards_rates
from honestspend.services.smart_charge import rank_charge


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
    row.safety_buffer = Decimal("0")
    row.budget_reserve_enabled = False
    s.commit()
    return s


def _checking(s, p, *, balance: Decimal, nickname: str = "Checking") -> Account:
    a = Account(
        profile_id=p.id,
        kind="checking",
        nickname=nickname,
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
    due_day: int | None,
    balance: Decimal = Decimal("0"),
    limit: Decimal = Decimal("10000"),
    close_day: int | None = 1,
    policy: str = "statement",
) -> Account:
    a = Account(
        profile_id=p.id,
        kind="credit",
        nickname=nickname,
        current_balance=balance,
        credit_limit=limit,
        available_credit=limit - balance,
        payment_due_day=due_day,
        statement_close_day=close_day,
        autopay_policy=policy,
        min_payment=Decimal("25"),
    )
    s.add(a)
    s.flush()
    return a


def test_safe_rewards_card_beats_cash(tmp_path: Path, monkeypatch):
    # checking 5000 Safe, card due in 25d, 5% groceries, 0% promo path, amount 100
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(s, p, nickname="Rewards", due_day=26)  # 2026-09-01 → 09-26 = 25d
    set_rewards_rates(s, card.id, {"groceries": 5, "general": 1})
    s.commit()

    rec = rank_charge(
        s,
        amount=Decimal("100"),
        reward_category="groceries",
        proposed_account_id=cash.id,
        as_of=AS_OF,
        profile_id=p.id,
        promo={"mode": "none"},
    )["recommended"]
    assert rec["method"] == "card"
    assert rec["account_name"] == "Rewards"
    s.close()


def test_card_that_squeezes_next_payment_loses_to_cash(tmp_path: Path, monkeypatch):
    # Safe 120, card next payment already 100, amount 80 → card unsafe, cash wins if cash ok
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("120"))
    card = _card(
        s,
        p,
        nickname="Squeeze",
        due_day=15,
        balance=Decimal("100"),
        policy="statement",
    )
    set_rewards_rates(s, card.id, {"general": 2})
    s.commit()

    res = rank_charge(
        s,
        amount=Decimal("80"),
        reward_category="general",
        proposed_account_id=card.id,
        as_of=AS_OF,
        profile_id=p.id,
    )
    proposed = res["proposed"]
    rec = res["recommended"]
    assert proposed["method"] == "card"
    assert proposed["safe"] is False
    assert rec["method"] == "cash"
    assert rec["safe"] is True
    assert rec["account_name"] == "Checking"
    s.close()


def test_more_float_beats_higher_rewards(tmp_path: Path, monkeypatch):
    # card A due in 40d 1% gas; card B due in 10d 5% gas → A wins
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _checking(s, p, balance=Decimal("5000"))
    # Monthly due-day maxes near 31d; A still has longer float than B.
    card_a = _card(s, p, nickname="Card A", due_day=30)  # 29d from Sep 1
    card_b = _card(s, p, nickname="Card B", due_day=11)  # 10d from Sep 1
    set_rewards_rates(s, card_a.id, {"gas": 1, "general": 1})
    set_rewards_rates(s, card_b.id, {"gas": 5, "general": 1})
    s.commit()

    rec = rank_charge(
        s,
        amount=Decimal("50"),
        reward_category="gas",
        proposed_account_id=card_b.id,
        as_of=AS_OF,
        profile_id=p.id,
    )["recommended"]
    assert rec["method"] == "card"
    assert rec["account_name"] == "Card A"
    s.close()


def test_missing_due_day_unsafe(tmp_path: Path, monkeypatch):
    # card no due day → proposed.safe is False, reason mentions due day
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _checking(s, p, balance=Decimal("5000"))
    card = _card(s, p, nickname="No Due", due_day=None, close_day=None)
    set_rewards_rates(s, card.id, {"general": 5})
    s.commit()

    res = rank_charge(
        s,
        amount=Decimal("40"),
        reward_category="general",
        proposed_account_id=card.id,
        as_of=AS_OF,
        profile_id=p.id,
    )
    proposed = res["proposed"]
    assert proposed["safe"] is False
    assert "due day" in (proposed["reason"] or "").lower()
    s.close()
