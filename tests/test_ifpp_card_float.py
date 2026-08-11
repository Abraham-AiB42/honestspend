"""Multi-card float honesty: best single card, not sum of every card."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from financial_os.engine.ifpp import CashAccountView, CardView, compute_ifpp


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
