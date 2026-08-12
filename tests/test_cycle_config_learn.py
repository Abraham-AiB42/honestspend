"""Cycle config defaults + import-safe learning (non-destructive when source=user)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.cycle_config import (
    apply_credit_cycle_defaults,
    default_funding_account_id,
    suggest_funding_from_payment_history,
)
from honestspend.services.setup_discover import apply_discoveries
from honestspend.services import plaid_service
from honestspend.db import PlaidItem


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


def test_user_source_frozen_against_import_defaults(tmp_path: Path, monkeypatch):
    """cycle_config_source=user must never auto-change close/due/funding/policy."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash_a = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Primary",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    cash_b = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Other",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=False,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="User Card",
        current_balance=Decimal("100"),
        statement_close_day=18,
        payment_due_day=25,
        autopay_policy="min",
        cycle_config_source="user",
    )
    s.add_all([cash_a, cash_b, card])
    s.flush()
    card.payment_funding_account_id = cash_b.id
    s.commit()

    res = apply_credit_cycle_defaults(
        s,
        card,
        source="import",
        statement_close_day=1,
        payment_due_day=15,
        autopay_policy="statement",
        payment_funding_account_id=cash_a.id,
    )
    s.commit()
    s.refresh(card)

    assert res["changed"] is False
    assert res.get("reason") == "user_locked"
    assert card.statement_close_day == 18
    assert card.payment_due_day == 25
    assert card.autopay_policy == "min"
    assert card.payment_funding_account_id == cash_b.id
    assert card.cycle_config_source == "user"
    s.close()


def test_import_creates_card_with_defaults(tmp_path: Path, monkeypatch):
    """New credit with empty cycle fields gets close=1, due=15, policy=statement, first checking."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops Checking",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Import Visa",
        current_balance=Decimal("0"),
    )
    s.add_all([cash, card])
    s.flush()

    res = apply_credit_cycle_defaults(s, card, source="import")
    s.commit()
    s.refresh(card)

    assert res["changed"] is True
    assert card.statement_close_day == 1
    assert card.payment_due_day == 15
    assert card.autopay_policy == "statement"
    assert card.payment_funding_account_id == cash.id
    assert card.cycle_config_source == "import"
    s.close()


def test_body_close_due_override_defaults(tmp_path: Path, monkeypatch):
    """Close/due from body when present; else 1/15."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Body Card",
        current_balance=Decimal("0"),
    )
    s.add_all([cash, card])
    s.flush()

    apply_credit_cycle_defaults(
        s,
        card,
        source="default",
        statement_close_day=18,
        payment_due_day=28,
    )
    s.commit()
    s.refresh(card)

    assert card.statement_close_day == 18
    assert card.payment_due_day == 28
    assert card.autopay_policy == "statement"
    assert card.payment_funding_account_id == cash.id
    assert card.cycle_config_source == "default"
    s.close()


def test_fill_missing_only_does_not_overwrite_non_user(tmp_path: Path, monkeypatch):
    """Non-user cards keep existing close/due/policy/funding; only fill blanks."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Partial",
        current_balance=Decimal("0"),
        statement_close_day=12,
        payment_due_day=None,
        autopay_policy="promo_sink",
        cycle_config_source="plaid",
    )
    s.add_all([cash, card])
    s.flush()

    apply_credit_cycle_defaults(s, card, source="import", statement_close_day=1, payment_due_day=15)
    s.commit()
    s.refresh(card)

    assert card.statement_close_day == 12  # preserved
    assert card.payment_due_day == 15  # filled
    assert card.autopay_policy == "promo_sink"  # preserved
    assert card.payment_funding_account_id == cash.id  # filled
    assert card.cycle_config_source == "import"  # updated on change
    s.close()


def test_setup_discover_applies_funding_and_source(tmp_path: Path, monkeypatch):
    """Discover apply creates credit with funding + cycle_config_source (non-user)."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.commit()

    res = apply_discoveries(
        s,
        profile_id=p.id,
        accepted=[
            {
                "type": "credit",
                "name": "Capital One",
                "median_amount": "220",
                "cadence": "monthly",
                "suggested_next_date": "2026-09-01",
                "payment_option": "statement",
                "selected": True,
            }
        ],
    )
    s.commit()
    assert res["count"] == 1
    card = s.query(Account).filter(Account.kind == "credit").one()
    assert card.statement_close_day == 1
    assert card.payment_due_day == 15
    assert card.autopay_policy == "statement"
    assert card.payment_funding_account_id == cash.id
    assert card.cycle_config_source in ("import", "default")
    s.close()


def test_plaid_new_credit_gets_cycle_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Plaid Cash",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    item = PlaidItem(
        profile_id=p.id,
        item_id="item-cc",
        access_token="access-cc",
        institution_name="Bank",
        status="active",
    )
    s.add(item)
    s.flush()

    accounts_payload = {
        "accounts": [
            {
                "account_id": "plaid-cc-new",
                "name": "Platinum",
                "type": "credit",
                "subtype": "credit card",
                "balances": {"current": 150.0, "limit": 5000.0, "available": 4850.0},
            }
        ]
    }
    with patch.object(plaid_service, "_post", return_value=accounts_payload):
        out, missing = plaid_service._sync_accounts(s, item)
    s.commit()

    assert missing == 0
    assert len(out) == 1
    card = out[0]
    assert card.kind == "credit"
    assert card.statement_close_day == 1
    assert card.payment_due_day == 15
    assert card.autopay_policy == "statement"
    assert card.payment_funding_account_id == cash.id
    assert card.cycle_config_source == "plaid"
    s.close()


def test_plaid_existing_user_locked_not_overwritten(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    item = PlaidItem(
        profile_id=p.id,
        item_id="item-locked",
        access_token="access-locked",
        institution_name="Bank",
        status="active",
    )
    s.add_all([cash, item])
    s.flush()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Locked CC",
        current_balance=Decimal("50"),
        statement_close_day=20,
        payment_due_day=10,
        autopay_policy="fixed",
        payment_funding_account_id=cash.id,
        cycle_config_source="user",
        plaid_item_pk=item.id,
        plaid_account_id="plaid-locked-cc",
    )
    s.add(card)
    s.commit()

    accounts_payload = {
        "accounts": [
            {
                "account_id": "plaid-locked-cc",
                "name": "Locked CC",
                "type": "credit",
                "subtype": "credit card",
                "balances": {"current": 75.0, "limit": 2000.0},
            }
        ]
    }
    with patch.object(plaid_service, "_post", return_value=accounts_payload):
        plaid_service._sync_accounts(s, item)
    s.commit()
    s.refresh(card)

    assert card.statement_close_day == 20
    assert card.payment_due_day == 10
    assert card.autopay_policy == "fixed"
    assert card.cycle_config_source == "user"
    s.close()


def test_suggest_funding_from_payment_to_card(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash_main = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Main",
        current_balance=Decimal("4000"),
        is_cash_for_ifpp=True,
    )
    cash_other = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Side",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=False,
    )
    s.add_all([cash_main, cash_other])
    s.flush()
    base = date(2026, 5, 1)
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash_other.id,
                txn_date=base + timedelta(days=30 * i),
                amount=Decimal("-200.00"),
                payee="Payment to Amazon Card",
            )
        )
    s.commit()

    suggested = suggest_funding_from_payment_history(s, p.id, "Amazon Card")
    assert suggested == cash_other.id

    # Defaults prefer payment history over first checking
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Amazon Card",
        current_balance=Decimal("0"),
    )
    s.add(card)
    s.flush()
    apply_credit_cycle_defaults(s, card, source="import")
    s.commit()
    s.refresh(card)
    assert card.payment_funding_account_id == cash_other.id
    s.close()


def test_suggest_funding_ignores_card_name_without_payment_phrasing(
    tmp_path: Path, monkeypatch
):
    """Card name alone in payee (no payment/transfer phrasing) must not match."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Main",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    base = date(2026, 5, 1)
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                txn_date=base + timedelta(days=30 * i),
                amount=Decimal("-45.00"),
                payee="Amazon Card store purchase",
            )
        )
    s.commit()

    assert suggest_funding_from_payment_history(s, p.id, "Amazon Card") is None
    s.close()


def test_default_funding_prefers_checking(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    sav = Account(
        profile_id=p.id,
        kind="savings",
        nickname="Savings",
        current_balance=Decimal("8000"),
        is_cash_for_ifpp=True,
    )
    chk = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Checking",
        current_balance=Decimal("1200"),
        is_cash_for_ifpp=False,
    )
    s.add_all([sav, chk])
    s.flush()
    assert default_funding_account_id(s, p.id) == chk.id
    s.close()


def test_bank_csv_import_fills_missing_cycle_on_credit(tmp_path: Path, monkeypatch):
    """CSV import into a bare credit account applies import-safe cycle defaults."""
    from honestspend.services.bank_csv import import_bank_csv

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Cash",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="CSV Card",
        current_balance=Decimal("0"),
    )
    s.add_all([cash, card])
    s.flush()
    s.commit()

    csv_path = tmp_path / "card.csv"
    csv_path.write_text(
        "Date,Description,Amount\n2026-07-01,Store,-50.00\n",
        encoding="utf-8",
    )
    import_bank_csv(
        s,
        account_id=card.id,
        file_obj=csv_path,
        filename="card.csv",
        auto_categorize=False,
    )
    s.commit()
    s.refresh(card)

    assert card.statement_close_day == 1
    assert card.payment_due_day == 15
    assert card.autopay_policy == "statement"
    assert card.payment_funding_account_id == cash.id
    assert card.cycle_config_source == "import"
    s.close()
