"""Parse Amazon/ISB plan tables and intro promo periods from statement text."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from honestspend.services.import_bootstrap import extract_statement_enrichment
from honestspend.services.promo_statement_parse import extract_promo_terms

AMAZON_ISB = """
Interest Saving Balance
Plan                  Remaining    Monthly payment    Payments left
Amazon Mixmaster      $348.12      $29.01             12
"""

INTRO_PROMO = (
    "Promotional APR 0% through 03/15/2027  Balance subject to promo $2,100.00"
)

# Card-level enrichment sample (APR / limit / min) plus an ISB table row.
COMPOSED = """
Chase Freedom Unlimited
Purchase APR 22.99%
Credit Limit $10,000.00
Minimum Payment Due $35.00
Payment Due Date 08/15/2026

Interest Saving Balance
Plan                  Remaining    Monthly payment    Payments left
Amazon Mixmaster      $348.12      $29.01             12
"""


def test_amazon_isb_table_purchase_plan():
    rows = extract_promo_terms(AMAZON_ISB)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "purchase_plan"
    assert row["name"] == "Amazon Mixmaster"
    assert row["principal_remaining"] == Decimal("348.12")
    assert row["monthly_payment"] == Decimal("29.01")
    assert row.get("months_left") == 12


def test_intro_apr_card_intro():
    rows = extract_promo_terms(INTRO_PROMO)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "card_intro"
    assert row["principal_remaining"] == Decimal("2100.00")
    assert row["end_date"] == date(2027, 3, 15)
    assert row.get("apr") == Decimal("0") or row.get("apr") == Decimal("0.0")


def test_compose_with_statement_enrichment():
    """Card-level extract_statement_enrichment still works when parse is composed."""
    enrich = extract_statement_enrichment(COMPOSED)
    promos = extract_promo_terms(COMPOSED)

    assert enrich.get("credit_limit") == Decimal("10000.00") or enrich.get("credit_limit") == Decimal(
        "10000"
    )
    assert enrich.get("apr") is not None
    assert enrich.get("min_payment") == Decimal("35.00") or enrich.get("min_payment") == Decimal("35")

    assert any(
        r["kind"] == "purchase_plan"
        and r["name"] == "Amazon Mixmaster"
        and r["principal_remaining"] == Decimal("348.12")
        and r["monthly_payment"] == Decimal("29.01")
        for r in promos
    )


def test_lone_zero_percent_does_not_invent_rows():
    """Never invent rows from a lone '0%' word without a balance or table."""
    assert extract_promo_terms("This card has 0% interest") == []
    assert extract_promo_terms("Enjoy 0% APR!") == []
    assert extract_promo_terms("") == []
