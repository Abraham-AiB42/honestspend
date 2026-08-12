"""Multi-card float honesty: best single card, not sum of every card."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from honestspend.engine.ifpp import CashAccountView, CardView, card_safe_to_charge, compute_ifpp


def test_promo_float_reserves_existing_promo_balance():
    """Existing promo debt must reduce interest-free new-charge capacity."""
    as_of = date.today()
    card = CardView(
        id=10,
        name="Promo Card",
        balance=Decimal("1000"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4000"),
        statement_close_day=1,
        payment_due_day=15,
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=200),
        promo_balance=Decimal("1000"),
    )
    plan = card_safe_to_charge(
        card,
        as_of=as_of,
        cash_available_for_payoff=Decimal("1000"),
        schedule=[],
        mode="conservative",
    )
    # $1k cash already needed for $1k promo balloon — cannot safely charge another $1k
    assert plan.safe_to_charge == Decimal("0.00")


def test_promo_float_allows_only_residual_cash():
    as_of = date.today()
    card = CardView(
        id=10,
        name="Promo Card",
        balance=Decimal("400"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4600"),
        statement_close_day=1,
        payment_due_day=15,
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=200),
        promo_balance=Decimal("400"),
    )
    plan = card_safe_to_charge(
        card,
        as_of=as_of,
        cash_available_for_payoff=Decimal("1000"),
        schedule=[],
        mode="conservative",
    )
    assert plan.safe_to_charge == Decimal("600.00")


def test_card_float_is_best_not_sum():
    cash = [
        CashAccountView(id=1, name="Checking", balance=Decimal("1000"), safety_buffer=Decimal("0")),
    ]
    cards = [
        CardView(
            id=10,
            name="Card A",
            balance=Decimal("0"),
            credit_limit=Decimal("5000"),
            available_credit=Decimal("5000"),
            statement_close_day=1,
            payment_due_day=15,
            promo_apr=Decimal("0"),
            promo_end_date=date.today().replace(year=date.today().year + 1),
            rewards_score=10,
        ),
        CardView(
            id=11,
            name="Card B",
            balance=Decimal("0"),
            credit_limit=Decimal("5000"),
            available_credit=Decimal("5000"),
            statement_close_day=1,
            payment_due_day=20,
            promo_apr=Decimal("0"),
            promo_end_date=date.today().replace(year=date.today().year + 1),
            rewards_score=5,
        ),
    ]
    r = compute_ifpp(
        as_of=date.today(),
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=cards,
        schedule=[],
        horizon_days=45,
    )
    # Each card alone could claim ~1000, but float must not be ~2000
    assert r.card_float_interest_free <= r.cash_spendable + Decimal("0.01")
    assert r.combined_purchasing_power == r.cash_spendable + r.card_float_interest_free
    assert r.details.get("card_float_mode") == "best_single_card"
    sum_caps = Decimal(str(r.details.get("sum_card_caps_not_used") or "0"))
    if sum_caps > r.card_float_interest_free + Decimal("1"):
        assert any("best single card" in w.lower() or "not the sum" in w.lower() for w in r.warnings)
