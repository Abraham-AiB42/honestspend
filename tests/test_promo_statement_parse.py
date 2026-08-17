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


# Synthetic issuer layouts only — do not paste customer statements here.
WF_PROMO = """
New Balance $1,000.00
PROMOTIONAL BALANCE
PROMOTIONAL RATE EXPIRES 08/07/26 0.00% $0.00 30 $0.00 $50.00
PROMOTIONAL BALANCE
PROMOTIONAL RATE EXPIRES 05/07/27 0.00% $0.00 30 $0.00 $900.00
"""

AMEX_INTRO = """
Introductory Purchase
Rate Expires 03/22/2027 then will go to 16.74% (v)
0.00% $400.00 $0.00
"""

HD_DEFERRED = """
You must pay your promotional balance of $300.00 in full by 04/02/27 to avoid paying deferred interest charges.
You must pay your promotional balance of $40.00 in full by 06/02/27 to avoid paying deferred interest charges.
"""

CHASE_ISB = """
New Balance: $800.00
Interest Saving Balance: $200.00
Minimum Payment Due: $35.00
Equal Pay Promo 0.00% (d) 07/15/26 $80.00
Equal Pay Promo 0.00% (d) - $60.00
Equal Pay Promo 0.00% (d) - $40.00
"""


def test_wells_promotional_rate_expires():
    rows = extract_promo_terms(WF_PROMO)
    bals = {r["principal_remaining"] for r in rows}
    assert Decimal("900.00") in bals
    assert Decimal("50.00") in bals
    ends = {r.get("end_date") for r in rows}
    assert date(2027, 5, 7) in ends


def test_amex_introductory_purchase():
    rows = extract_promo_terms(AMEX_INTRO)
    assert any(
        r["kind"] == "card_intro"
        and r["principal_remaining"] == Decimal("400.00")
        and r.get("end_date") == date(2027, 3, 22)
        for r in rows
    )


def test_homedepot_deferred_interest():
    rows = extract_promo_terms(HD_DEFERRED)
    assert len(rows) >= 2
    assert any(r["principal_remaining"] == Decimal("300.00") for r in rows)
    assert any(r.get("end_date") == date(2027, 4, 2) for r in rows)


def test_chase_equal_pay_and_isb():
    from honestspend.services.promo_statement_parse import extract_interest_saving_balance

    rows = extract_promo_terms(CHASE_ISB)
    plans = [r for r in rows if r["kind"] == "purchase_plan"]
    assert len(plans) >= 3
    assert {r["principal_remaining"] for r in plans} >= {
        Decimal("80.00"),
        Decimal("60.00"),
        Decimal("40.00"),
    }
    assert extract_interest_saving_balance(CHASE_ISB) == Decimal("200.00")


APPLE_INSTALLMENTS = """
Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD)
07/31/2026,07/31/2026,"MONTHLY INSTALLMENTS (4 OF 6)","Monthly Installments (4 Of 6)","Installment","Installment","24.83"
07/31/2026,07/31/2026,"MONTHLY INSTALLMENTS (4 OF 6)","Monthly Installments (4 Of 6)","Installment","Installment","24.83"
07/31/2026,07/31/2026,"MONTHLY INSTALLMENTS (11 OF 24)","Monthly Installments (11 Of 24)","Installment","Installment","41.62"
07/31/2026,07/31/2026,"MONTHLY INSTALLMENTS (11 OF 24)","Monthly Installments (11 Of 24)","Installment","Installment","58.29"
06/30/2026,06/30/2026,"MONTHLY INSTALLMENTS (3 OF 6)","Monthly Installments (3 Of 6)","Installment","Installment","24.83"
06/30/2026,06/30/2026,"MONTHLY INSTALLMENTS (10 OF 24)","Monthly Installments (10 Of 24)","Installment","Installment","41.62"
"""


def test_apple_monthly_installments_are_purchase_plans():
    from honestspend.services.promo_statement_parse import parse_monthly_installment

    row = parse_monthly_installment("MONTHLY INSTALLMENTS (11 OF 24)", Decimal("41.62"))
    assert row is not None
    assert row["kind"] == "purchase_plan"
    assert row["name"] == "Monthly installments · 24 mo"
    assert row["monthly_payment"] == Decimal("41.62")
    assert row["months_left"] == 14
    assert row["principal_remaining"] == Decimal("41.62") * 14


def test_apple_csv_groups_parallel_same_amount_plans():
    rows = extract_promo_terms(APPLE_INSTALLMENTS)
    plans = [r for r in rows if r.get("source_kind") == "monthly_installment"]
    by_monthly = {r["monthly_payment"]: r for r in plans}
    # Two 6-mo $24.83 plans on the latest month → one $49.66 plan, 3 left
    six = by_monthly[Decimal("49.66")]
    assert six["months_left"] == 3
    assert six["principal_remaining"] == Decimal("49.66") * 3
    assert by_monthly[Decimal("41.62")]["months_left"] == 14
    assert by_monthly[Decimal("41.62")]["principal_remaining"] == Decimal("41.62") * 14
    assert by_monthly[Decimal("58.29")]["months_left"] == 14
    # History (10 of 24 / 3 of 6) must not invent extra plans
    assert len(plans) == 3


def test_merge_keeps_combined_plan_when_dedupe_drops_a_twin():
    from honestspend.services.promo_statement_parse import merge_installment_terms

    from_file = extract_promo_terms(APPLE_INSTALLMENTS)
    from_txns = extract_promo_terms(
        '07/31/2026 "MONTHLY INSTALLMENTS (4 OF 6)" 24.83\n'
        '07/31/2026 "MONTHLY INSTALLMENTS (11 OF 24)" 41.62\n'
        '07/31/2026 "MONTHLY INSTALLMENTS (11 OF 24)" 58.29\n'
    )
    merged = merge_installment_terms(
        [r for r in from_file if r.get("source_kind") == "monthly_installment"],
        [r for r in from_txns if r.get("source_kind") == "monthly_installment"],
    )
    by_monthly = {r["monthly_payment"] for r in merged}
    assert Decimal("49.66") in by_monthly
    assert Decimal("24.83") not in by_monthly
    assert Decimal("41.62") in by_monthly
    assert Decimal("58.29") in by_monthly


def test_plan_it_payments_of_is_purchase_plan():
    rows = extract_promo_terms(
        "Plan It\n4 payments of $37.50\nPayment 2 of 4\n"
    )
    plans = [r for r in rows if r["kind"] == "purchase_plan"]
    assert any(
        r["monthly_payment"] == Decimal("37.50") and r.get("months_left") == 3 for r in plans
    )


def test_capone_new_balance_and_month_name_due():
    text = (
        "Payment Due Date: Aug 17, 2026 Account ending in 4411 "
        "New Balance $150.00 Minimum Payment Due $25.00"
    )
    en = extract_statement_enrichment(text)
    assert en.get("new_balance") == Decimal("150.00")
    assert en.get("payment_due_day") == 17
    assert en.get("min_payment") in (Decimal("25.00"), Decimal("25"))
