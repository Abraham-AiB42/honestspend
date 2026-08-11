"""Utilization, estimated interest, and educational credit advice."""

from __future__ import annotations

from decimal import Decimal

from financial_os.db import Account
from financial_os.services.card_honesty import (
    amount_to_utilization_target,
    credit_advice_for_policy,
    estimated_min_payment,
    estimated_monthly_interest,
    honesty_payload,
    utilization_pct,
)


def test_utilization_and_paydown_to_target():
    assert utilization_pct(Decimal("500"), Decimal("1000")) == Decimal("50.00")
    assert amount_to_utilization_target(
        Decimal("500"), Decimal("1000"), 30
    ) == Decimal("200.00")
    assert amount_to_utilization_target(
        Decimal("100"), Decimal("1000"), 30
    ) == Decimal("0.00")


def test_estimated_interest_and_min():
    # 12% APR on 1200 → 12.00 / month simple
    assert estimated_monthly_interest(Decimal("1200"), Decimal("0.12")) == Decimal(
        "12.00"
    )
    assert estimated_monthly_interest(Decimal("1200"), Decimal("12")) == Decimal(
        "12.00"
    )
    assert estimated_min_payment(Decimal("1000"), None) == Decimal("25.00")
    assert estimated_min_payment(Decimal("2000"), None) == Decimal("40.00")
    assert estimated_min_payment(Decimal("2000"), Decimal("35")) == Decimal("35.00")


def test_books_advice_mentions_promo_and_float():
    tips = credit_advice_for_policy(
        "books", "day_before_close", has_promo=True
    )
    joined = " ".join(tips).lower()
    assert "promo" in joined or "0%" in joined
    assert "score" in joined or "fico" in joined or "vantage" in joined
    assert "not" in joined  # does not calculate score


def test_honesty_payload_shape():
    a = Account(
        profile_id=1,
        kind="credit",
        nickname="X",
        current_balance=Decimal("500"),
        credit_limit=Decimal("1000"),
        apr=Decimal("0.24"),
        min_payment=Decimal("40"),
        autopay_policy="books",
        payment_timing="on_close",
    )
    h = honesty_payload(a, promo_remaining=Decimal("100"), soft_pct=10, hard_pct=30)
    assert h["min_payment"] == "40.00"
    assert h["min_payment_source"] == "issuer"
    assert h["utilization_pct"] == "50.00"
    assert h["amount_to_util_hard"] == "200.00"
    assert h["amount_to_util_soft"] == "400.00"
    assert isinstance(h["advice"], list) and h["advice"]
    assert "Educational" in h["disclaimer"]
