"""Wave 1: never-neg write gate, red-now, rescue coach, fee confirm."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Transaction, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.liquidity_rescue import build_rescue_plan
from financial_os.services.profiles import create_profile


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


def test_txn_would_go_negative_409(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 100},
    )
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    r = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-01",
            "amount": "-500",
            "payee": "BIG",
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "would_go_negative"
    assert detail["confirm_required"] is True


def test_txn_confirm_unsafe_allows(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 100},
    )
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    r = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-01",
            "amount": "-500",
            "payee": "BIG",
            "confirm_unsafe": True,
        },
    )
    assert r.status_code == 200, r.text
    bal = next(a for a in client.get("/api/accounts").json() if a["id"] == cash["id"])
    assert Decimal(str(bal["current_balance"])) < 0


def test_hard_enforcement_blocks_even_with_confirm(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 50},
    )
    client.patch("/api/settings", json={"never_negative_enforcement": "hard"})
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    r = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-01",
            "amount": "-200",
            "payee": "BIG",
            "confirm_unsafe": True,
        },
    )
    assert r.status_code == 409


def test_is_red_now_on_negative_checking(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        s.add(
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="P",
                current_balance=Decimal("-10"),
                is_cash_for_ifpp=True,
            )
        )
        s.commit()
        r = run_ifpp(s, as_of=date(2026, 8, 8), profile_id=personal.id, scope="entity")
        assert r.is_red_now is True
        assert r.next_red_day == date(2026, 8, 8)


def test_rescue_includes_transfer_before_revolve(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        s.add(
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="Checking",
                current_balance=Decimal("50"),
                is_cash_for_ifpp=True,
            )
        )
        s.add(
            Account(
                profile_id=personal.id,
                kind="savings",
                nickname="HYSA",
                current_balance=Decimal("5000"),
                apy=Decimal("0.05"),
                is_cash_for_ifpp=True,
            )
        )
        s.add(
            Account(
                profile_id=personal.id,
                kind="credit",
                nickname="Card",
                current_balance=Decimal("100"),
                credit_limit=Decimal("5000"),
                available_credit=Decimal("4900"),
                apr=Decimal("0.24"),
                payment_due_day=15,
                statement_close_day=1,
            )
        )
        s.commit()
        plan = build_rescue_plan(
            s, amount=Decimal("2000"), profile_id=personal.id, scope="entity"
        )
        actions = [o["action"] for o in plan["options"]]
        assert "transfer_in" in actions
        # transfer should rank before revolve
        ti = next(i for i, o in enumerate(plan["options"]) if o["action"] == "transfer_in")
        revs = [i for i, o in enumerate(plan["options"]) if o["action"] == "card_revolve_apr"]
        if revs:
            assert ti < revs[0]


def test_fee_confirm_dismiss(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 5000},
    )
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    r = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-01",
            "amount": "-35",
            "payee": "BANK LATE FEE",
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    fees = client.get("/api/fees/candidates").json()
    assert fees["count"] >= 1
    conf = client.post(
        "/api/fees/confirm",
        json={"transaction_id": tid, "action": "dismiss"},
    )
    assert conf.status_code == 200, conf.text
    fees2 = client.get("/api/fees/candidates").json()
    ids = [c["transaction_id"] for c in fees2.get("candidates", [])]
    assert tid not in ids


def test_liquidity_rescue_api(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 200,
            "card_limit": 3000,
            "card_name": "Card",
            "card_due_day": 15,
        },
    )
    r = client.post("/api/liquidity/rescue", json={"amount": 500})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "options" in body
    assert body["shortfall"] is not None
