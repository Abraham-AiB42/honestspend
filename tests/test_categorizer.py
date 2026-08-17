from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Account, Base, Category, CategoryRule, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.categorizer import (
    categorize_uncategorized,
    learn_rule_from_correction,
    match_rule,
    suggest_category,
)


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_seed_rules_exist(tmp_path: Path):
    s = _session(tmp_path)
    assert s.query(CategoryRule).filter(CategoryRule.source == "seed").count() > 10
    s.close()


def test_shell_maps_to_gas(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    txn = Transaction(
        profile_id=personal.id,
        account_id=acct.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-45"),
        payee="SHELL OIL 12345",
    )
    s.add(txn)
    s.flush()
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.source in ("rule", "catalog")
    assert sug.category_name == "Gas (personal)"
    assert sug.confidence >= 0.85
    s.close()


def test_batch_apply_high_confidence(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("-15"),
            payee="NETFLIX.COM",
        )
    )
    s.flush()
    results = categorize_uncategorized(s, apply=True, use_grok=False, min_confidence=0.85)
    assert results[0]["applied"] is True
    txn = s.query(Transaction).first()
    assert txn.category_id is not None
    s.close()


def test_learn_rule_from_correction(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    cat = s.query(Category).filter(Category.code == "PER_DINING").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    txn = Transaction(
        profile_id=personal.id,
        account_id=acct.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal("-22"),
        payee="LOCAL TACO SHOP XYZ",
    )
    s.add(txn)
    s.flush()
    rule = learn_rule_from_correction(s, txn, cat.id)
    assert rule is not None
    assert rule.source == "learned"
    txn2 = Transaction(
        profile_id=personal.id,
        account_id=acct.id,
        txn_date=date(2026, 8, 2),
        amount=Decimal("-18"),
        payee="LOCAL TACO SHOP XYZ #2",
    )
    s.add(txn2)
    s.flush()
    sug = suggest_category(s, txn2, use_grok=False)
    assert sug.category_id == cat.id
    s.close()


def test_match_types():
    r = CategoryRule(match_type="starts_with", pattern="amzn", category_id=1)
    assert match_rule(r, "amzn mktp us") is True
    assert match_rule(r, "xx amzn") is False


def _cash_txn(s, payee: str, memo: str = "", amount: str = "-20"):
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = (
        s.query(Account).filter(Account.profile_id == personal.id).first()
        or Account(
            profile_id=personal.id,
            kind="checking",
            nickname="C",
            current_balance=Decimal("100"),
            is_cash_for_ifpp=True,
        )
    )
    if acct.id is None:
        s.add(acct)
        s.flush()
    txn = Transaction(
        profile_id=personal.id,
        account_id=acct.id,
        txn_date=date(2026, 8, 1),
        amount=Decimal(amount),
        payee=payee,
        memo=memo or None,
    )
    s.add(txn)
    s.flush()
    return s, txn


def test_web_payment_is_card_payment(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "Web Payment", amount="-370.92")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "SYS_CC_PAYMENT"
    assert sug.confidence >= 0.9
    s.close()


def test_automatic_payment_thank_you(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "AUTOMATIC PAYMENT - THANK YOU", amount="97.00")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "SYS_CC_PAYMENT"
    s.close()


def test_payment_to_mortgage_is_housing_not_card(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "Payment to Rocket Mortgage", amount="-1600")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_HOUSING"
    s.close()


def test_assoc_pmt_is_hoa(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "SAMPLE HOA TYPE: Assoc Pmt CO: SAMPLE HOA", amount="-130")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_HOA"
    s.close()


def test_share_transfer(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "From Share 01", amount="75")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "SYS_TRANSFER"
    s.close()


def test_interest_charge(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "INTEREST CHARGE ON PURCHASES", amount="-18.40")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_CC_INTEREST"
    s.close()


def test_bank_label_office_shipping_personal(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(
        s,
        "OFFICE SUPPLY CO",
        memo="XLSX:activity.xlsx · cat:Office & Shipping",
        amount="-64.56",
    )
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_SHOPPING"
    s.close()


def test_bank_label_phone_cable(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(
        s,
        "LOCAL FIBER ISP",
        memo="CSV:sample.csv · cat:Phone/Cable · type:Sale",
        amount="-99.95",
    )
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_CELL"
    s.close()


def test_bank_type_payment_wins_unknown_payee(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(
        s,
        "UNKNOWN ISSUER CREDIT",
        memo="CSV:card.csv · cat: · type:Payment",
        amount="169.90",
    )
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "SYS_CC_PAYMENT"
    s.close()


def test_merchant_doordash(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "DOORDASH*TACOBELL", amount="-22.10")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "PER_DINING"
    s.close()


def test_catalog_statement_aliases(tmp_path: Path):
    """National statement descriptors from public issuer / NRF / Technomic lists."""
    from honestspend.services.merchant_catalog import suggest_from_merchants

    cases = [
        ("WM SUPERCENTER #1234", "PER_SHOPPING"),
        ("TST* DOWNTOWN BISTRO", "PER_DINING"),
        ("PANDA EXPRESS 1842", "PER_DINING"),
        ("PLANET FITNESS", "PER_HEALTHCARE"),
        ("CHARGEPOINT", "PER_GAS"),
        ("DUKE ENERGY ONLINE", "PER_UTILITIES"),
        ("HBO MAX", "PER_SUBSCRIPTIONS"),
        ("CHEWY.COM", "PER_SHOPPING"),
        ("APPLE STORE #R123", "PER_SHOPPING"),
        ("AMZN MKTP US", "PER_SHOPPING"),
    ]
    for payee, code in cases:
        hit = suggest_from_merchants(payee)
        assert hit is not None, payee
        assert hit.code == code, (payee, hit.code, code)


def test_directpay_full_balance_is_card_payment(tmp_path: Path):
    s = _session(tmp_path)
    s, txn = _cash_txn(s, "DIRECTPAY FULL BALANCE", amount="467.68")
    sug = suggest_category(s, txn, use_grok=False)
    assert sug.category_code == "SYS_CC_PAYMENT"
    s.close()


def test_dining_youtube_rumble_farmers_catalog():
    from honestspend.services.merchant_catalog import (
        suggest_from_merchants,
        suggest_from_payee_heuristics,
    )

    assert suggest_from_payee_heuristics("DIRECTPAY FULL BALANCE").code == "SYS_CC_PAYMENT"
    assert suggest_from_merchants("ROCK BOTTOM RESTAURALOVELAND").code == "PER_DINING"
    assert suggest_from_merchants("FP *RUMBLE USA INC").code == "PER_SUBSCRIPTIONS"
    assert suggest_from_merchants("DOMINO'S").code == "PER_DINING"
    assert suggest_from_merchants("PIZZA STREET").code == "PER_DINING"
    assert suggest_from_merchants("LOCAL RESTAURANT DOWNTOWN").code == "PER_DINING"
    assert suggest_from_merchants("GOOGLE *YouTubePremium").code == "PER_SUBSCRIPTIONS"
    assert suggest_from_merchants("FARMERS INS BILLING").code == "PER_INSURANCE"


def test_catalog_starlink_installment_dmv_speakeasy():
    from honestspend.services.merchant_catalog import (
        suggest_from_merchants,
        suggest_from_payee_heuristics,
    )

    assert suggest_from_merchants("STALINK").code == "PER_CELL"
    assert suggest_from_merchants("STARLINK INTERNET").code == "PER_CELL"
    assert suggest_from_merchants("CO MOTOR VEH SERV EMDENVER").code == "PER_AUTO"
    assert suggest_from_merchants("SPEAKEASY CO DENVER").code == "PER_DINING"
    assert suggest_from_merchants("LOCAL TAVERN DOWNTOWN").code == "PER_DINING"
    hit = suggest_from_payee_heuristics("MONTHLY INSTALLMENTS (11 OF 24)")
    assert hit is None or hit.code != "SYS_TRANSFER"


def test_shell_does_not_match_inside_unrelated_word(tmp_path: Path):
    from honestspend.services.merchant_catalog import suggest_from_merchants

    hit = suggest_from_merchants("SEASHELL BOUTIQUE")
    assert hit is None or hit.code != "PER_GAS"


def test_learned_rule_beats_catalog(tmp_path: Path):
    s = _session(tmp_path)
    dining = s.query(Category).filter(Category.code == "PER_DINING").one()
    s, txn = _cash_txn(s, "AMZN MKTP US", amount="-12")
    learn_rule_from_correction(s, txn, dining.id)
    txn2 = Transaction(
        profile_id=txn.profile_id,
        account_id=txn.account_id,
        txn_date=date(2026, 8, 2),
        amount=Decimal("-9"),
        payee="AMZN MKTP US *OTHER",
    )
    s.add(txn2)
    s.flush()
    sug = suggest_category(s, txn2, use_grok=False)
    assert sug.category_id == dining.id
    assert sug.source == "rule"
    s.close()
