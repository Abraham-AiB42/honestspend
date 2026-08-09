"""Wave 2: void, transfer match, import presets, simulate, child allowance."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import Account, Transaction, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.profiles import create_profile
from financial_os.services.transfer_match import confirm_transfer_pair, find_transfer_candidates
from financial_os.services.txn_void import void_transaction
from financial_os.services.intermix import apply_intermix


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


def test_void_reverses_balance(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 1000})
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    r = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-01",
            "amount": "-100",
            "payee": "Coffee",
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    bal1 = next(a for a in client.get("/api/accounts").json() if a["id"] == cash["id"])
    assert Decimal(str(bal1["current_balance"])) == Decimal("900")

    v = client.post(f"/api/transactions/{tid}/void", json={"reason": "mistake"})
    assert v.status_code == 200, v.text
    assert v.json()["status"] == "void"
    bal2 = next(a for a in client.get("/api/accounts").json() if a["id"] == cash["id"])
    assert Decimal(str(bal2["current_balance"])) == Decimal("1000")

    # idempotent
    v2 = client.post(f"/api/transactions/{tid}/void", json={})
    assert v2.json().get("already_void") is True


def test_transfer_match_and_confirm(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        a1 = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="A",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        a2 = Account(
            profile_id=personal.id,
            kind="savings",
            nickname="B",
            current_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
        s.add_all([a1, a2])
        s.flush()
        t_out = Transaction(
            profile_id=personal.id,
            account_id=a1.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("-200"),
            payee="TO SAVINGS",
            status="cleared",
        )
        t_in = Transaction(
            profile_id=personal.id,
            account_id=a2.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("200"),
            payee="FROM CHECKING",
            status="cleared",
        )
        s.add_all([t_out, t_in])
        s.commit()
        cand = find_transfer_candidates(s, days=7, as_of=date(2026, 8, 5))
        assert cand["count"] >= 1
        pair = cand["candidates"][0]
        res = confirm_transfer_pair(s, out_txn_id=pair["out_txn_id"], in_txn_id=pair["in_txn_id"])
        assert res["ok"] is True
        s.refresh(t_out)
        s.refresh(t_in)
        assert t_out.is_transfer and t_in.is_transfer
        assert t_out.transfer_pair_id == t_in.id


def test_import_preset_api(client: TestClient):
    r = client.put(
        "/api/import/presets",
        json={
            "institution_key": "Demo Bank",
            "amount_sign": "invert",
            "mapping": {"date_col": "Date"},
            "notes": "expenses positive in export",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount_sign"] == "invert"
    lst = client.get("/api/import/presets").json()
    assert any(p["institution_key"] == "demo bank" for p in lst["presets"])


def test_ifpp_simulate_reduces_spendable(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    r = client.post(
        "/api/ifpp/simulate",
        json={
            "extra_outflows": [
                {"on_date": "2026-08-15", "amount": 800, "name": "What-if purchase"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "baseline" in body and "simulated" in body
    base = Decimal(body["baseline"]["cash_spendable"])
    sim = Decimal(body["simulated"]["cash_spendable"])
    assert sim <= base


def test_child_allowance_intermix(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("financial_os.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        child = create_profile(
            s, display_name="Kid", entity_type="child", parent_profile_id=personal.id
        )
        p_acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="P",
            current_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
        c_acct = Account(
            profile_id=child.id,
            kind="checking",
            nickname="Allowance",
            current_balance=Decimal("0"),
            is_cash_for_ifpp=True,
        )
        s.add_all([p_acct, c_acct])
        s.flush()
        res = apply_intermix(
            s,
            kind="child_allowance",
            amount=Decimal("25"),
            from_account_id=p_acct.id,
            to_account_id=c_acct.id,
        )
        assert res["ok"] is True
        s.refresh(p_acct)
        s.refresh(c_acct)
        assert p_acct.current_balance == Decimal("475")
        assert c_acct.current_balance == Decimal("25")
