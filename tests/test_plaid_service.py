"""Plaid snapshot balance honesty (mocked HTTP — no live Plaid)."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, PlaidItem, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services import plaid_service


def _session(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    seed_all(s)
    return s


def _item(s, profile_id: int) -> PlaidItem:
    item = PlaidItem(
        profile_id=profile_id,
        item_id="item-test-1",
        access_token="access-test",
        institution_name="Test Bank",
        status="active",
    )
    s.add(item)
    s.flush()
    return item


def test_sync_accounts_sets_institution_and_books(tmp_path: Path, monkeypatch):
    from financial_os.config import settings

    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    item = _item(s, personal.id)

    accounts_payload = {
        "accounts": [
            {
                "account_id": "plaid-chk-1",
                "name": "Checking",
                "type": "depository",
                "subtype": "checking",
                "balances": {"current": 1500.25, "available": 1400.0},
            }
        ]
    }

    with patch.object(plaid_service, "_post", return_value=accounts_payload):
        out, missing = plaid_service._sync_accounts(s, item)

    assert missing == 0
    assert len(out) == 1
    acct = out[0]
    assert acct.plaid_account_id == "plaid-chk-1"
    assert acct.current_balance == Decimal("1500.25")
    assert acct.institution_balance == Decimal("1500.25")
    assert acct.kind == "checking"
    s.close()


def test_sync_accounts_credit_available_fallback_on_create(tmp_path: Path, monkeypatch):
    from financial_os.config import settings

    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    item = _item(s, personal.id)

    accounts_payload = {
        "accounts": [
            {
                "account_id": "plaid-cc-1",
                "name": "Visa",
                "type": "credit",
                "subtype": "credit card",
                "balances": {"current": 200.0, "limit": 1000.0},  # no available
            }
        ]
    }

    with patch.object(plaid_service, "_post", return_value=accounts_payload):
        out, missing = plaid_service._sync_accounts(s, item)

    assert missing == 0
    acct = out[0]
    assert acct.kind == "credit"
    assert acct.current_balance == Decimal("200.0")
    assert acct.institution_balance == Decimal("200.0")
    assert acct.credit_limit == Decimal("1000.0")
    assert acct.available_credit == Decimal("800.0")
    s.close()


def test_sync_accounts_missing_current_counted(tmp_path: Path, monkeypatch):
    """Existing linked account with no balances.current leaves books unchanged."""
    from financial_os.config import settings

    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    item = _item(s, personal.id)
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Linked",
        current_balance=Decimal("900"),
        institution_balance=Decimal("900"),
        is_cash_for_ifpp=True,
        plaid_item_pk=item.id,
        plaid_account_id="plaid-chk-miss",
    )
    s.add(acct)
    s.flush()

    accounts_payload = {
        "accounts": [
            {
                "account_id": "plaid-chk-miss",
                "name": "Checking",
                "type": "depository",
                "subtype": "checking",
                "balances": {},  # no current
            }
        ]
    }
    with patch.object(plaid_service, "_post", return_value=accounts_payload):
        out, missing = plaid_service._sync_accounts(s, item)
    assert missing == 1
    s.refresh(acct)
    assert acct.current_balance == Decimal("900")
    assert acct.institution_balance == Decimal("900")
    s.close()


def test_upsert_plaid_txn_does_not_change_balance(tmp_path: Path, monkeypatch):
    from financial_os.config import settings

    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    item = _item(s, personal.id)
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Linked",
        current_balance=Decimal("1000"),
        institution_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
        plaid_item_pk=item.id,
        plaid_account_id="plaid-chk-2",
    )
    s.add(acct)
    s.flush()
    bal_before = acct.current_balance

    txn = {
        "transaction_id": "txn-1",
        "account_id": "plaid-chk-2",
        "amount": 42.10,  # Plaid outflow
        "date": "2026-08-01",
        "name": "COFFEE",
        "pending": False,
    }
    ok = plaid_service._upsert_plaid_txn(s, item, {"plaid-chk-2": acct}, txn)
    assert ok is True
    s.refresh(acct)
    assert acct.current_balance == bal_before  # snapshot-only; no delta apply
    assert s.query(Transaction).count() == 1
    t = s.query(Transaction).one()
    assert t.amount == Decimal("-42.10")
    s.close()


def test_sync_transactions_refreshes_balance_without_double_apply(tmp_path: Path, monkeypatch):
    from financial_os.config import settings

    monkeypatch.setattr(settings, "plaid_client_id", "cid")
    monkeypatch.setattr(settings, "plaid_secret", "sec")
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    item = _item(s, personal.id)

    def fake_post(path: str, payload: dict):
        if path == "/accounts/get":
            return {
                "accounts": [
                    {
                        "account_id": "plaid-chk-3",
                        "name": "Checking",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 900.0},
                    }
                ]
            }
        if path == "/transactions/sync":
            return {
                "added": [
                    {
                        "transaction_id": "txn-a",
                        "account_id": "plaid-chk-3",
                        "amount": 50.0,
                        "date": "2026-08-02",
                        "name": "STORE",
                        "pending": False,
                    }
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "c1",
                "has_more": False,
            }
        raise AssertionError(path)

    with patch.object(plaid_service, "_post", side_effect=fake_post):
        result = plaid_service.sync_transactions(s, item, auto_categorize=False)

    assert result["added"] == 1
    acct = s.query(Account).filter(Account.plaid_account_id == "plaid-chk-3").one()
    # Balance is snapshot 900, not 900-50 from txn
    assert acct.current_balance == Decimal("900.0")
    assert acct.institution_balance == Decimal("900.0")
    s.close()
