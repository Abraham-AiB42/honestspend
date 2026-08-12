from datetime import date, timedelta
from decimal import Decimal

from honestspend.services.credit_sim import CreditProfileInputs, simulate_credit
from honestspend.services.debt_strategy import (
    DebtView,
    YieldAccountView,
    compare_strategies,
    resolve_opportunity_rate,
    simulate_payoff,
)


def _card(id, name, bal, apr, limit, **kw):
    return DebtView(
        id=id,
        name=name,
        kind="credit",
        balance=Decimal(str(bal)),
        apr=Decimal(str(apr)),
        min_payment=Decimal(str(kw.get("min", 50))),
        credit_limit=Decimal(str(limit)),
        promo_apr=kw.get("promo_apr"),
        promo_end_date=kw.get("promo_end"),
        promo_balance=kw.get("promo_bal"),
        priority_rank=kw.get("rank", 100),
        opened_date=kw.get("opened"),
    )


def test_avalanche_orders_highest_apr_first():
    debts = [
        _card(1, "LowAPR", 2000, 0.12, 5000),
        _card(2, "HighAPR", 1500, 0.29, 5000),
        _card(3, "Mid", 3000, 0.19, 5000),
    ]
    plan = simulate_payoff(debts, strategy="avalanche", extra_monthly=Decimal("200"))
    assert plan.order[0].name == "HighAPR"
    assert plan.order[1].name == "Mid"
    assert plan.order[2].name == "LowAPR"


def test_snowball_orders_smallest_balance():
    debts = [
        _card(1, "Big", 5000, 0.29, 10000),
        _card(2, "Tiny", 300, 0.12, 2000),
    ]
    plan = simulate_payoff(debts, strategy="snowball")
    assert plan.order[0].name == "Tiny"


def test_promo_guard_prioritizes_expiring_zero():
    as_of = date(2026, 8, 5)
    debts = [
        _card(1, "HotAPR", 1000, 0.29, 5000),
        _card(
            2,
            "ZeroSoon",
            2000,
            0.2499,
            10000,
            promo_apr=Decimal("0"),
            promo_end=as_of + timedelta(days=40),
            promo_bal=Decimal("2000"),
        ),
    ]
    plan = simulate_payoff(debts, strategy="promo_guard", as_of=as_of)
    assert plan.order[0].name == "ZeroSoon"
    assert plan.order[0].effective_apr == Decimal("0")


def test_custom_priority_rank():
    debts = [
        _card(1, "A", 1000, 0.29, 5000, rank=50),
        _card(2, "B", 1000, 0.10, 5000, rank=1),
    ]
    plan = simulate_payoff(debts, strategy="custom")
    assert plan.order[0].name == "B"


def test_compare_avalanche_beats_snowball_on_interest():
    debts = [
        _card(1, "High", 3000, 0.28, 8000, min=75),
        _card(2, "Low", 800, 0.10, 3000, min=25),
    ]
    cmp = compare_strategies(debts, extra_monthly=Decimal("300"))
    by = {c["strategy"]: c for c in cmp}
    assert Decimal(by["avalanche"]["estimated_interest"]) <= Decimal(
        by["snowball"]["estimated_interest"]
    )


def test_credit_sim_high_util_hurts_score():
    good = [
        _card(1, "A", 200, 0.2, 10000, opened=date(2018, 1, 1)),
    ]
    bad = [
        _card(1, "A", 9000, 0.2, 10000, opened=date(2018, 1, 1)),
    ]
    sg = simulate_credit(good, CreditProfileInputs())
    sb = simulate_credit(bad, CreditProfileInputs())
    assert sg.score > sb.score
    assert 300 <= sg.score <= 850
    assert "NOT an official" in sg.disclaimer


def test_credit_sim_payment_history():
    debts = [_card(1, "A", 500, 0.2, 5000, opened=date(2020, 1, 1))]
    clean = simulate_credit(debts, CreditProfileInputs(on_time_rate=Decimal("1")))
    dirty = simulate_credit(
        debts,
        CreditProfileInputs(on_time_rate=Decimal("0.9"), late_30_12m=3, late_90_plus_12m=1),
    )
    assert clean.score > dirty.score


def test_mortgage_below_cash_yield_is_minimum_only():
    """2.625% mortgage vs 6% HYSA → do not prepay mortgage."""
    debts = [
        DebtView(
            id=1,
            name="Mortgage",
            kind="loan",
            balance=Decimal("300000"),
            apr=Decimal("0.02625"),
            min_payment=Decimal("1500"),
        ),
        _card(2, "Amex", 4000, 0.22, 10000, min=100),
    ]
    plan = simulate_payoff(
        debts,
        strategy="avalanche",
        extra_monthly=Decimal("1000"),
        opportunity_rate=Decimal("0.06"),
        opportunity_rate_source="HYSA 6%",
        opportunity_cost_aware=True,
    )
    mortgage = next(o for o in plan.order if o.name == "Mortgage")
    amex = next(o for o in plan.order if o.name == "Amex")
    assert mortgage.recommendation == "minimum_only"
    assert mortgage.beats_opportunity is False
    assert amex.recommendation == "extra_ok"
    assert amex.beats_opportunity is True
    # Extra should target Amex, not mortgage
    assert plan.order[0].name == "Amex" or amex.strategy_rank < mortgage.strategy_rank
    assert any("2.625" in n or "opportunity" in n.lower() or "hurdle" in n.lower() for n in plan.notes)


def test_resolve_opportunity_from_yield_accounts():
    rate, src = resolve_opportunity_rate(
        [
            YieldAccountView(1, "Primary checking", Decimal("1000"), Decimal("0.001")),
            YieldAccountView(2, "High-yield savings", Decimal("5000"), Decimal("0.06")),
        ]
    )
    assert rate == Decimal("0.06")
    assert "High-yield savings" in src


def test_without_opportunity_avalanche_still_orders_apr():
    debts = [
        DebtView(1, "Mortgage", "loan", Decimal("100000"), Decimal("0.02625"), Decimal("800")),
        _card(2, "Card", 2000, 0.20, 5000),
    ]
    plan = simulate_payoff(
        debts,
        strategy="avalanche",
        opportunity_cost_aware=False,
    )
    assert plan.order[0].name == "Card"
