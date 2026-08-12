"""I charged it — pre-purchase commit endpoint (writes charge + optional plan)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import (
    Account,
    AppSettings,
    Profile,
    PromoInstallmentLine,
    Transaction,
    init_db,
)
from honestspend.seed import seed_all
from honestspend.services.rewards_pick import set_rewards_rates
from honestspend.services.smart_charge import rank_charge


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
    funding_id: int | None = None,
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
        payment_funding_account_id=funding_id,
    )
    s.add(a)
    s.flush()
    return a


def _due_day_in(days: int, as_of: date | None = None) -> int:
    """Calendar due day ~days ahead of as_of (clamped 1..28)."""
    as_of = as_of or date.today()
    target = as_of + timedelta(days=days)
    return min(28, max(1, target.day))


def test_commit_recommended_creates_card_charge(tmp_path: Path, monkeypatch):
    from honestspend.services.pre_purchase_commit import commit_purchase

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(
        s,
        p,
        nickname="Rewards",
        due_day=_due_day_in(25),
        funding_id=cash.id,
    )
    set_rewards_rates(s, card.id, {"groceries": 5, "general": 1})
    s.commit()

    ranked = rank_charge(
        s,
        amount=Decimal("100"),
        reward_category="groceries",
        proposed_account_id=cash.id,
        profile_id=p.id,
        promo={"mode": "none"},
    )
    rec = ranked["recommended"]
    assert rec["method"] == "card"
    assert rec["account_id"] == card.id

    out = commit_purchase(
        s,
        amount=Decimal("100"),
        post_account_id=card.id,
        proposed_account_id=cash.id,
        recommended_account_id=card.id,
        reward_category="groceries",
        promo={"mode": "none"},
        profile_id=p.id,
        payee="Grocery Mart",
    )
    s.commit()

    txn = out["transaction"]
    assert txn["account_id"] == card.id
    assert Decimal(str(txn["amount"])) == Decimal("-100.00")
    assert out["promo_line"] is None
    assert out["posted"]["account_id"] == card.id
    assert out["recommended"]["account_id"] == card.id

    s.refresh(card)
    assert Decimal(str(card.current_balance)) == Decimal("100.00")
    rows = s.query(Transaction).filter(Transaction.account_id == card.id).all()
    assert len(rows) == 1
    s.close()


def test_commit_proposed_cash_creates_cash_txn(tmp_path: Path, monkeypatch):
    from honestspend.services.pre_purchase_commit import commit_purchase

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(
        s,
        p,
        nickname="Rewards",
        due_day=_due_day_in(25),
        funding_id=cash.id,
    )
    set_rewards_rates(s, card.id, {"general": 3})
    s.commit()

    ranked = rank_charge(
        s,
        amount=Decimal("40"),
        reward_category="general",
        proposed_account_id=cash.id,
        profile_id=p.id,
    )
    rec_id = ranked["recommended"]["account_id"]

    out = commit_purchase(
        s,
        amount=Decimal("40"),
        post_account_id=cash.id,
        proposed_account_id=cash.id,
        recommended_account_id=rec_id,
        reward_category="general",
        profile_id=p.id,
        payee="Corner Store",
    )
    s.commit()

    txn = out["transaction"]
    assert txn["account_id"] == cash.id
    assert Decimal(str(txn["amount"])) == Decimal("-40.00")
    s.refresh(cash)
    assert Decimal(str(cash.current_balance)) == Decimal("4960.00")
    s.close()


def test_purchase_plan_commit_creates_line_and_next_payment(
    tmp_path: Path, monkeypatch
):
    from honestspend.services.pre_purchase_commit import commit_purchase
    from honestspend.services.statement_cycle import project_card_payment

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(
        s,
        p,
        nickname="Plan Card",
        due_day=_due_day_in(20),
        balance=Decimal("0"),
        funding_id=cash.id,
        policy="statement",
    )
    set_rewards_rates(s, card.id, {"general": 1})
    s.commit()

    before = project_card_payment(s, card.id)
    before_next = Decimal(str(before.get("next_payment") or 0))

    amount = Decimal("600")
    monthly = Decimal("50")
    out = commit_purchase(
        s,
        amount=amount,
        post_account_id=card.id,
        proposed_account_id=card.id,
        recommended_account_id=card.id,
        reward_category="general",
        promo={"mode": "purchase_plan", "months": 12, "monthly": str(monthly)},
        profile_id=p.id,
        payee="Amazon",
    )
    s.commit()

    assert out["promo_line"] is not None
    line = out["promo_line"]
    assert line["kind"] == "purchase_plan"
    assert line["source"] == "user"
    assert line["status"] == "open"
    assert Decimal(str(line["principal_remaining"])) == amount
    assert Decimal(str(line["monthly_payment"])) == monthly
    assert line["linked_txn_id"] == out["transaction"]["id"]

    db_line = s.query(PromoInstallmentLine).one()
    assert db_line.linked_txn_id == out["transaction"]["id"]
    assert db_line.monthly_payment == monthly

    after = project_card_payment(s, card.id)
    after_next = Decimal(str(after.get("next_payment") or 0))
    assert after_next == before_next + monthly
    s.refresh(card)
    assert Decimal(str(card.next_payment_amount_cached or 0)) == after_next
    s.close()


def test_unsafe_without_confirm_raises(tmp_path: Path, monkeypatch):
    from honestspend.services.pre_purchase_commit import (
        UnsafePurchase,
        commit_purchase,
    )

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    # Missing due day → card unsafe
    card = _card(
        s,
        p,
        nickname="No Due",
        due_day=None,
        close_day=None,
        funding_id=cash.id,
    )
    set_rewards_rates(s, card.id, {"general": 5})
    s.commit()

    ranked = rank_charge(
        s,
        amount=Decimal("40"),
        reward_category="general",
        proposed_account_id=card.id,
        profile_id=p.id,
    )
    assert ranked["proposed"]["safe"] is False
    rec = ranked["recommended"]
    assert rec is not None and rec["account_id"] != card.id

    with pytest.raises(UnsafePurchase) as ei:
        commit_purchase(
            s,
            amount=Decimal("40"),
            post_account_id=card.id,
            proposed_account_id=card.id,
            recommended_account_id=rec["account_id"],
            reward_category="general",
            profile_id=p.id,
            confirm_unsafe=False,
        )
    payload = ei.value.payload
    assert payload.get("code") in ("unsafe_purchase", "would_go_negative") or "safe" in str(
        payload
    ).lower() or "due" in str(payload).lower()
    assert s.query(Transaction).count() == 0
    s.close()


def test_double_commit_key_does_not_double_post(tmp_path: Path, monkeypatch):
    from honestspend.services.pre_purchase_commit import commit_purchase

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(
        s,
        p,
        nickname="Rewards",
        due_day=_due_day_in(25),
        funding_id=cash.id,
    )
    set_rewards_rates(s, card.id, {"general": 2})
    s.commit()

    key = "client-abc-123"
    kwargs = dict(
        amount=Decimal("75"),
        post_account_id=card.id,
        proposed_account_id=card.id,
        recommended_account_id=card.id,
        reward_category="general",
        profile_id=p.id,
        commit_key=key,
        payee="Store",
    )
    first = commit_purchase(s, **kwargs)
    s.commit()
    second = commit_purchase(s, **kwargs)
    s.commit()

    assert first["transaction"]["id"] == second["transaction"]["id"]
    assert s.query(Transaction).filter(Transaction.account_id == card.id).count() == 1
    s.refresh(card)
    assert Decimal(str(card.current_balance)) == Decimal("75.00")
    s.close()
