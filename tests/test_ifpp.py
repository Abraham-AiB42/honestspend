from datetime import date, timedelta
from decimal import Decimal

from financial_os.engine.ifpp import (
    CardView,
    CashAccountView,
    ScheduledView,
    compute_ifpp,
    is_promo_active,
    recommend_card_for_purchase,
)


def test_conservative_ignores_expected_income():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("1000"), True)]
    schedule = [
        ScheduledView(1, "Rent", Decimal("-800"), as_of + timedelta(days=3), "fixed"),
        ScheduledView(2, "Maybe pay", Decimal("2000"), as_of + timedelta(days=2), "expected"),
    ]
    result = compute_ifpp(
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=[],
        schedule=schedule,
        horizon_days=30,
    )
    # 1000 - 800 rent = 200 min; expected income ignored
    assert result.cash_spendable == Decimal("200.00")
    assert result.next_red_day is None


def test_expected_mode_counts_income():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("100"), True)]
    schedule = [
        ScheduledView(1, "Bill", Decimal("-500"), as_of + timedelta(days=5), "fixed"),
        ScheduledView(2, "Paycheck", Decimal("2000"), as_of + timedelta(days=2), "fixed"),
    ]
    result = compute_ifpp(
        as_of=as_of,
        mode="expected",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=[],
        schedule=schedule,
        horizon_days=30,
    )
    assert result.cash_spendable >= Decimal("100")
    assert result.next_red_day is None


def test_red_day_when_bill_exceeds_cash():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("200"), True)]
    schedule = [
        ScheduledView(1, "Insurance", Decimal("-500"), as_of + timedelta(days=4), "fixed"),
    ]
    result = compute_ifpp(
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=[],
        schedule=schedule,
    )
    assert result.cash_spendable == Decimal("0.00")
    assert result.next_red_day == as_of + timedelta(days=4)


def test_safety_buffer():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("1000"), True)]
    result = compute_ifpp(
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("300"),
        cash_accounts=cash,
        cards=[],
        schedule=[],
    )
    assert result.cash_spendable == Decimal("700.00")


def test_promo_zero_percent_active():
    as_of = date(2026, 8, 5)
    card = CardView(
        id=1,
        name="Amex 0%",
        balance=Decimal("1000"),
        credit_limit=Decimal("10000"),
        available_credit=Decimal("9000"),
        statement_close_day=1,
        payment_due_day=20,
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=180),
        promo_balance=Decimal("1000"),
    )
    assert is_promo_active(card, as_of) is True


def test_card_blocked_without_due_day():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("5000"), True)]
    card = CardView(
        id=2,
        name="Mystery",
        balance=Decimal("0"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("5000"),
        statement_close_day=None,
        payment_due_day=None,
    )
    result = compute_ifpp(
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=[card],
        schedule=[],
    )
    assert result.cards[0].safe_to_charge == Decimal("0.00")
    assert result.cards[0].risk == "blocked"


def test_statement_payoff_limits_charge():
    as_of = date(2026, 8, 5)
    cash = [CashAccountView(1, "Checking", Decimal("1000"), True)]
    card = CardView(
        id=3,
        name="Discover",
        balance=Decimal("400"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4600"),
        statement_close_day=10,
        payment_due_day=25,
        apr=Decimal("0.229"),
    )
    result = compute_ifpp(
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=cash,
        cards=[card],
        schedule=[],
    )
    # Can charge up to cash - existing balance path: 1000 - 400 = 600
    assert result.cards[0].safe_to_charge == Decimal("600.00")
    assert "Pay statement in full" in result.cards[0].payoff_path


def test_recommend_card_rewards():
    plans_result = compute_ifpp(
        as_of=date(2026, 8, 5),
        mode="conservative",
        safety_buffer=Decimal("0"),
        cash_accounts=[CashAccountView(1, "C", Decimal("3000"), True)],
        cards=[
            CardView(1, "Low rewards", Decimal("0"), Decimal("5000"), Decimal("5000"), 1, 15, rewards_score=1),
            CardView(2, "High rewards", Decimal("0"), Decimal("5000"), Decimal("5000"), 1, 15, rewards_score=10),
        ],
        schedule=[],
    )
    cards = [
        CardView(1, "Low rewards", Decimal("0"), Decimal("5000"), Decimal("5000"), 1, 15, rewards_score=1),
        CardView(2, "High rewards", Decimal("0"), Decimal("5000"), Decimal("5000"), 1, 15, rewards_score=10),
    ]
    pick = recommend_card_for_purchase(Decimal("100"), plans_result.cards, cards)
    assert pick is not None
    assert pick.name == "High rewards"
