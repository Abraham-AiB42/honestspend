"""BNPL activity → finite future charges; issuer split tables → purchase plans."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, ScheduledItem, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.bnpl import (
    apply_bnpl_for_account,
    bnpl_brand,
    is_bnpl_payee,
    parse_bnpl_payee,
    quote_bnpl,
)
from honestspend.services.pre_purchase import check_purchase
from honestspend.services.pre_purchase_commit import commit_purchase
from honestspend.services.coming_up import build_coming_up
from honestspend.services.merchant_catalog import suggest_from_payee_heuristics
from honestspend.services.promo_statement_parse import extract_promo_terms


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
    if row:
        row.safety_buffer = Decimal("0")
        row.budget_reserve_enabled = False
    s.commit()
    return s


def test_brands_and_zip_is_not_a_postal_code():
    assert bnpl_brand("KLARNA *NIKE 2 OF 4") == "Klarna"
    assert bnpl_brand("AFFIRM *BEST BUY 3 OF 12") == "Affirm"
    assert bnpl_brand("AFTERPAY 1/4") == "Afterpay"
    assert bnpl_brand("SEZZLE*URBAN 2 OF 4") == "Sezzle"
    assert bnpl_brand("PAYPAL *PAY IN 4 NIKE") == "PayPal Pay in 4"
    assert bnpl_brand("PP*PAYIN4 STORE") == "PayPal Pay in 4"
    assert bnpl_brand("ZIP CO *STORE 2 OF 4") == "Zip"
    assert bnpl_brand("QUADPAY *STORE") == "Zip"
    assert not is_bnpl_payee("USPS PRIORITY 80202")
    assert not is_bnpl_payee("ZIP LINE TOURS")
    assert not is_bnpl_payee("PAYPAL *ETSY")


def test_parse_remaining_excludes_this_posted_installment():
    row = parse_bnpl_payee("KLARNA *NIKE 2 OF 4", Decimal("25.00"))
    assert row is not None
    assert row["remaining_after"] == 2
    assert row["monthly_payment"] == Decimal("25.00")
    assert row["cadence"] == "biweekly"
    long = parse_bnpl_payee("AFFIRM *SOFA 3 OF 12", Decimal("80.00"))
    assert long is not None
    assert long["remaining_after"] == 9
    assert long["cadence"] == "monthly"


def test_klarna_n_of_m_creates_finite_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("800"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    posted = date(2026, 8, 1)
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=posted,
            amount=Decimal("-25.00"),
            payee="KLARNA *NIKE 2 OF 4",
        )
    )
    s.commit()
    out = apply_bnpl_for_account(s, cash.id, as_of=posted)
    assert out["created"] == 1
    row = s.query(ScheduledItem).filter(ScheduledItem.account_id == cash.id).one()
    assert row.amount == Decimal("-25.00")
    assert row.cadence == "biweekly"
    assert row.next_date == posted + timedelta(days=14)
    assert row.end_date == posted + timedelta(days=28)
    assert row.active
    assert (row.notes or "").startswith("bnpl:")
    s.close()


def test_last_installment_ends_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("400"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=date(2026, 7, 1),
            amount=Decimal("-25.00"),
            payee="AFTERPAY 3 OF 4",
        )
    )
    s.commit()
    apply_bnpl_for_account(s, cash.id, as_of=date(2026, 7, 1))
    assert s.query(ScheduledItem).filter(ScheduledItem.active.is_(True)).count() == 1
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            txn_date=date(2026, 7, 15),
            amount=Decimal("-25.00"),
            payee="AFTERPAY 4 OF 4",
        )
    )
    s.commit()
    apply_bnpl_for_account(s, cash.id, as_of=date(2026, 7, 15))
    live = s.query(ScheduledItem).filter(ScheduledItem.active.is_(True)).count()
    assert live == 0
    s.close()


def test_issuer_split_table_is_purchase_plan_not_bnpl_schedule():
    text = """
    Plan It
    4 payments of $37.50
    Payment 2 of 4
    """
    rows = extract_promo_terms(text)
    plans = [r for r in rows if r["kind"] == "purchase_plan"]
    assert plans
    assert plans[0]["monthly_payment"] == Decimal("37.50")
    assert plans[0]["months_left"] == 3
    assert plans[0]["principal_remaining"] == Decimal("37.50") * 3


def test_klarna_statement_does_not_become_card_carve_out():
    text = "KLARNA *NIKE  4 payments of $25.00  Payment 2 of 4"
    rows = extract_promo_terms(text)
    assert not any(r.get("source_kind") == "split_purchase" for r in rows)


def test_bnpl_is_shopping_not_transfer():
    hit = suggest_from_payee_heuristics("KLARNA *NIKE 2 OF 4")
    assert hit is not None
    assert hit.code == "PER_SHOPPING"
    assert not hit.is_transfer


def test_coming_up_lists_card_bnpl(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        credit_limit=Decimal("2000"),
    )
    s.add(card)
    s.flush()
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=card.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("-40.00"),
            payee="AFTERPAY *SOFA 1 OF 4",
        )
    )
    s.commit()
    apply_bnpl_for_account(s, card.id, as_of=date(2026, 8, 1))
    out = build_coming_up(
        s,
        profile_id=p.id,
        as_of=date(2026, 8, 10),
        mode="calendar",
        calendar_days=14,
    )
    names = [i.get("name") or "" for i in (out.get("items") or [])]
    assert any("Afterpay" in n or "AFTERPAY" in n for n in names)
    s.close()


def test_quote_pay_in_4_splits_evenly_and_adds_fees():
    q = quote_bnpl(Decimal("100.00"), provider="klarna", plan="pay_in_4")
    assert q["payments"] == 4
    assert q["cadence"] == "biweekly"
    assert Decimal(q["first_due"]) == Decimal("25.00")
    assert Decimal(q["remaining_total"]) == Decimal("75.00")
    assert Decimal(q["extra_cost"]) == Decimal("0.00")
    assert Decimal(q["late_fee_typical"]) == Decimal("7.00")
    assert q["late_fee_included"] is False
    with_fee = quote_bnpl(
        Decimal("100.00"),
        provider="afterpay",
        plan="pay_in_4",
        origination_fee=Decimal("4.00"),
    )
    assert Decimal(with_fee["principal"]) == Decimal("104.00")
    assert Decimal(with_fee["total_cost"]) == Decimal("104.00")
    assert Decimal(with_fee["extra_cost"]) == Decimal("4.00")
    assert Decimal(with_fee["first_due"]) == Decimal("26.00")


def test_quote_affirm_apr_has_finance_charge():
    q = quote_bnpl(
        Decimal("1200.00"),
        provider="affirm",
        plan="monthly",
        payments=12,
        apr=Decimal("12"),
    )
    assert q["cadence"] == "monthly"
    assert Decimal(q["finance_charge"]) > Decimal("0")
    assert Decimal(q["total_cost"]) > Decimal("1200.00")
    assert Decimal(q["first_due"]) == Decimal(q["installment"])


def test_can_i_buy_bnpl_first_payment_must_fit(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("40"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash)
    s.flush()
    s.commit()
    promo = {"mode": "bnpl", "provider": "klarna", "plan": "pay_in_4"}
    short = check_purchase(
        s,
        amount=Decimal("200"),
        proposed_account_id=cash.id,
        promo=promo,
        profile_id=p.id,
    )
    assert short["verdict"] == "do_not_buy"
    assert short["bnpl_quote"] is not None
    assert Decimal(short["bnpl_quote"]["first_due"]) == Decimal("50.00")
    bnpl_opts = [o for o in short["options"] if o.get("method") == "bnpl"]
    assert bnpl_opts and bnpl_opts[0]["safe"] is False

    cash.current_balance = Decimal("400")
    s.commit()
    ok = check_purchase(
        s,
        amount=Decimal("200"),
        proposed_account_id=cash.id,
        promo=promo,
        profile_id=p.id,
    )
    assert ok["recommended"]["method"] == "bnpl"
    assert ok["verdict"] == "safe"
    assert "Klarna" in (ok["why"] or "")
    s.close()


def test_commit_bnpl_posts_first_and_schedules_rest(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash)
    s.flush()
    s.commit()
    promo = {"mode": "bnpl", "provider": "afterpay", "plan": "pay_in_4"}
    out = commit_purchase(
        s,
        amount=Decimal("80"),
        post_account_id=cash.id,
        proposed_account_id=cash.id,
        recommended_account_id=cash.id,
        promo=promo,
        profile_id=p.id,
    )
    s.commit()
    assert Decimal(out["transaction"]["amount"]) == Decimal("-20.00")
    assert out["bnpl_schedule"] is not None
    assert out["posted"]["method"] == "bnpl"
    row = s.get(ScheduledItem, out["bnpl_schedule"]["id"])
    assert row is not None and row.active
    assert row.amount == Decimal("-20.00")
    assert row.cadence == "biweekly"
    s.refresh(cash)
    assert cash.current_balance == Decimal("480.00")
    s.close()
