from datetime import date, timedelta
from decimal import Decimal

from honestspend.engine.ifpp import CardView
from honestspend.services.payoff import plan_card_payoff


def test_promo_min_then_balloon():
    as_of = date(2026, 8, 5)
    card = CardView(
        id=1,
        name="Discover 0%",
        balance=Decimal("3000"),
        credit_limit=Decimal("10000"),
        available_credit=Decimal("7000"),
        statement_close_day=12,
        payment_due_day=8,
        apr=Decimal("0.2499"),
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=300),
        promo_balance=Decimal("3000"),
        min_payment=Decimal("75"),
    )
    plan = plan_card_payoff(card, as_of=as_of)
    assert plan.strategy == "promo_min_then_balloon"
    assert plan.interest_if_followed == Decimal("0")
    assert plan.balloon_amount > Decimal("0")
    assert any(s["action"] == "balloon_payoff" for s in plan.steps)
    assert any(s["action"] == "minimum_payment" for s in plan.steps)


def test_pay_in_full_no_promo():
    as_of = date(2026, 8, 5)
    card = CardView(
        id=2,
        name="Amex",
        balance=Decimal("500"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4500"),
        statement_close_day=1,
        payment_due_day=25,
        apr=Decimal("0.22"),
    )
    plan = plan_card_payoff(card, as_of=as_of)
    assert plan.strategy == "pay_in_full"
    assert plan.steps[0]["action"] == "pay_in_full"
    assert Decimal(plan.steps[0]["amount"]) == Decimal("500.00")


def test_zero_balance():
    card = CardView(
        id=3,
        name="Empty",
        balance=Decimal("0"),
        credit_limit=Decimal("1000"),
        available_credit=Decimal("1000"),
        statement_close_day=1,
        payment_due_day=15,
    )
    plan = plan_card_payoff(card, as_of=date(2026, 8, 5))
    assert plan.strategy == "paid_off"
