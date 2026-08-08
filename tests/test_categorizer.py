from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Base, Category, CategoryRule, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.categorizer import (
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
    assert sug.source == "rule"
    assert sug.category_name == "Gas (personal)"
    assert sug.confidence >= 0.9
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
