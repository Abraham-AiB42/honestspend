"""Payment matcher + glance API."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.payment_match import find_payment_candidates


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def test_glance_shape(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 2500})
    r = client.get("/api/glance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "cash_spendable" in body
    assert "safe_to_spend" in body  # Home parity (post budget reserve)
    assert "budget_reserve" in body
    assert "alerts" in body
    assert "pending" in body
    assert body.get("client_hint")


def test_payment_pair_detect(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Checking",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Card",
            current_balance=Decimal("200"),
            credit_limit=Decimal("5000"),
            payment_due_day=15,
        )
        s.add_all([cash, card])
        s.flush()
        # cash pays card: -200 from cash, +200 on card (reduces owed)
        t_cash = Transaction(
            profile_id=personal.id,
            account_id=cash.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("-200"),
            payee="CARD PAYMENT",
            status="cleared",
        )
        t_card = Transaction(
            profile_id=personal.id,
            account_id=card.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("200"),
            payee="PAYMENT THANK YOU",
            status="cleared",
        )
        s.add_all([t_cash, t_card])
        s.commit()
        cand = find_payment_candidates(s, days=14, as_of=date(2026, 8, 10))
        assert cand["count"] >= 1
        pair = next(c for c in cand["candidates"] if c["kind"] == "cash_to_card_pair")
        assert pair["cash_txn_id"] == t_cash.id
        assert pair["card_txn_id"] == t_card.id


def test_payment_confirm_api(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 2000, "card_name": "C", "card_limit": 3000, "card_due_day": 15},
    )
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    card = next(a for a in accts if a["kind"] == "credit")
    # allow negative would need confirm — amount within balance
    r1 = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-05",
            "amount": "-100",
            "payee": "CARD PAYMENT",
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/transactions",
        json={
            "profile_id": card["profile_id"],
            "account_id": card["id"],
            "txn_date": "2026-08-05",
            "amount": "100",
            "payee": "PAYMENT",
        },
    )
    assert r2.status_code == 200, r2.text
    conf = client.post(
        "/api/payments/confirm",
        json={"cash_txn_id": r1.json()["id"], "card_txn_id": r2.json()["id"]},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json().get("ok") is True


def test_pending_on_ifpp(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-06",
            "amount": "-75",
            "payee": "PENDING SHOP",
            "status": "pending",
        },
    )
    ifpp = client.get("/api/ifpp").json()
    assert int(ifpp.get("pending_count") or 0) >= 1
    assert ifpp.get("pending_warning")
