"""Setup categorize + recurring finalize."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Category, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.setup_categorize import (
    auto_apply_high_confidence,
    categorize_status,
    confirm_payee_category,
    prepare_categorize,
)
from honestspend.services.setup_recurring_finalize import (
    apply_recurring_choices,
    remaining_recurring,
)


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_confirm_payee_and_rule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        is_cash_for_ifpp=True,
        current_balance=Decimal("2000"),
    )
    s.add(cash)
    s.flush()
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    if not food:
        food = s.query(Category).filter(Category.profile_id == p.id).first()
    assert food is not None
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=date.today() - timedelta(days=i * 3),
                amount=Decimal("-12.50"),
                payee="STARBUCKS STORE 123",
            )
        )
    s.commit()

    st = categorize_status(s, profile_id=p.id)
    assert st["uncategorized_count"] >= 3
    assert st["confirm_queue"]

    res = confirm_payee_category(
        s,
        payee_key="starbucks store",
        category_id=food.id,
        create_rule=True,
        profile_id=p.id,
    )
    s.commit()
    assert res["updated"] >= 3
    assert res["status"]["uncategorized_count"] == 0
    recent = res["status"].get("recent_assignments") or []
    assert any(r.get("category_id") == food.id for r in recent)

    from honestspend.services.setup_categorize import undo_payee_category

    ids = res.get("transaction_ids") or []
    undo = undo_payee_category(s, transaction_ids=ids, rule_id=res.get("rule_id"), profile_id=p.id)
    s.commit()
    assert undo["cleared"] >= 3
    assert undo["status"]["uncategorized_count"] >= 3
    s.close()


def test_categorize_status_links_account_payments(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        is_cash_for_ifpp=True,
        current_balance=Decimal("20000"),
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="AiB42 Amex",
        institution="Blue Business Plus",
        current_balance=Decimal("11039.90"),
    )
    s.add_all([cash, card])
    s.flush()
    out = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date.today() - timedelta(days=2),
        amount=Decimal("-11039.90"),
        payee="Payment to American Express",
    )
    inn = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date.today() - timedelta(days=3),
        amount=Decimal("11039.90"),
        payee="ONLINE PAYMENT - THANK YOU",
    )
    s.add_all([out, inn])
    s.commit()

    auto = auto_apply_high_confidence(s, profile_id=p.id, use_grok=False)
    s.commit()
    st = auto["status"]
    s.refresh(out)
    s.refresh(inn)
    assert out.is_transfer and inn.is_transfer
    assert out.transfer_pair_id == inn.id
    assert not any(
        "american express" in (g.get("payee") or "").lower() for g in st["confirm_queue"]
    )
    recent = st.get("recent_assignments") or []
    assert any(
        r.get("to_account") == "AiB42 Amex" and "american express" in (r.get("payee") or "").lower()
        for r in recent
    )
    # Undo must not be re-linked by a following status GET
    from honestspend.services.setup_categorize import undo_payee_category

    undo_payee_category(s, transaction_ids=[out.id], profile_id=p.id)
    s.commit()
    categorize_status(s, profile_id=p.id)
    s.commit()
    s.refresh(out)
    s.refresh(inn)
    assert out.transfer_pair_id is None
    assert inn.transfer_pair_id is None
    assert "[skip-auto-link]" in (out.memo or "")
    s.close()


def test_business_chip_remap_restores_on_undo(tmp_path: Path, monkeypatch):
    from honestspend.services.profiles import create_profile

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    biz = create_profile(s, display_name="AP Agency", entity_type="business")
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Agency Checking",
        is_cash_for_ifpp=True,
        current_balance=Decimal("5000"),
    )
    s.add(cash)
    s.flush()
    marketing = (
        s.query(Category)
        .filter(Category.profile_id == biz.id, Category.display_name == "Marketing")
        .one()
    )
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    txn = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date.today(),
        amount=Decimal("-40"),
        payee="LIFT LOCAL",
    )
    s.add(txn)
    s.commit()
    confirm_payee_category(
        s,
        transaction_ids=[txn.id],
        category_id=marketing.id,
        profile_id=p.id,
    )
    s.commit()
    s.refresh(txn)
    assert txn.profile_id == biz.id
    assert txn.category_id == marketing.id

    from honestspend.services.setup_categorize import undo_payee_category

    undo_payee_category(s, transaction_ids=[txn.id], profile_id=p.id)
    s.commit()
    s.refresh(txn)
    assert txn.profile_id == p.id
    assert txn.category_id is None
    assert "[skip-auto-link]" not in (txn.memo or "")

    confirm_payee_category(
        s,
        transaction_ids=[txn.id],
        category_id=food.id,
        profile_id=p.id,
    )
    s.commit()
    s.refresh(txn)
    assert txn.profile_id == p.id
    assert txn.category_id == food.id
    s.close()


def test_promo_apr_lines_are_voided_not_queued(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Wells Fargo Reflect",
        current_balance=Decimal("100"),
    )
    s.add(card)
    s.flush()
    fake = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date.today(),
        amount=Decimal("-8189.34"),
        payee="0.00% $0.00 30 $0.00",
    )
    real = Transaction(
        profile_id=p.id,
        account_id=card.id,
        txn_date=date.today(),
        amount=Decimal("-12.00"),
        payee="STARBUCKS",
    )
    s.add_all([fake, real])
    s.commit()
    categorize_status(s, profile_id=p.id)
    s.commit()
    s.refresh(fake)
    assert fake.status != "void"
    prepare_categorize(s, profile_id=p.id)
    s.commit()
    st = categorize_status(s, profile_id=p.id)
    s.refresh(fake)
    s.refresh(real)
    assert fake.status == "void"
    assert real.category_id is None
    assert not any("0.00%" in (g.get("payee") or "") for g in st["confirm_queue"])
    s.close()


def test_checks_stay_separate(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        is_cash_for_ifpp=True,
        current_balance=Decimal("20000"),
    )
    s.add(cash)
    s.flush()
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    assert food is not None
    t100 = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date.today() - timedelta(days=2),
        amount=Decimal("-1500"),
        payee="Check # 100",
    )
    t101 = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        txn_date=date.today() - timedelta(days=1),
        amount=Decimal("-2100"),
        payee="CHECK # 101",
    )
    s.add_all([t100, t101])
    s.commit()

    st = categorize_status(s, profile_id=p.id)
    keys = {g["payee_key"] for g in st["confirm_queue"]}
    assert "check #100" in keys
    assert "check #101" in keys

    res = confirm_payee_category(
        s,
        payee_key="check #100",
        category_id=food.id,
        create_rule=True,
        profile_id=p.id,
    )
    s.commit()
    assert res["updated"] == 1
    assert res.get("rule_id") is None
    s.refresh(t100)
    s.refresh(t101)
    assert t100.category_id == food.id
    assert t101.category_id is None
    s.close()


def test_business_chips_include_everyday_labels(tmp_path: Path, monkeypatch):
    from honestspend.services.profiles import create_profile

    s = _session(tmp_path, monkeypatch)
    create_profile(s, display_name="AP Agency", entity_type="business")
    s.commit()
    st = categorize_status(s)
    chip_names = {c["name"] for c in st["category_chips"]}
    more_names = {c["name"] for c in st["more_chips"]}
    assert "Groceries" in chip_names
    for need in (
        "Marketing",
        "Services",
        "Equipment",
        "Supplies",
        "Travel",
        "Meals 50%",
        "Meals 100%",
        "Home office",
        "Phone / internet (business)",
        "Postage / shipping",
        "Client gifts",
        "Entertainment (nondeductible)",
        "Conferences / events",
        "Parking / tolls",
        "Rent (office / property)",
        "Rent (vehicles / equipment)",
        "Legal and professional services",
        "Car and truck (business)",
        "Software / SaaS / cloud",
        "Education / CE",
        "Insurance (other than health)",
        "Taxes and licenses",
        "Salaries and wages",
        "Officer compensation",
    ):
        assert need in more_names, need
        assert need not in chip_names, need
    s.close()


def test_category_lists_are_alphabetical(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = categorize_status(s)
    chips = [c["name"] for c in st["category_chips"]]
    more = [c["name"] for c in st["more_chips"]]
    assert chips == sorted(chips, key=str.casefold)
    assert more == sorted(more, key=str.casefold)
    s.close()


def test_auto_apply_runs(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    # seed has rules — just ensure API path doesn't crash
    out = auto_apply_high_confidence(s, use_grok=False, min_confidence=0.85)
    assert "applied" in out
    s.close()


def test_recurring_remaining_empty_ok(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    r = remaining_recurring(s)
    assert "suggestions" in r
    out = apply_recurring_choices(s, accepted=[])
    assert out["accepted"] == 0
    s.close()
