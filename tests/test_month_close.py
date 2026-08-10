"""Month-close + promo brief (dream H1-B / H1-C3)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from financial_os.config import settings
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "require_api_key", False)
    import financial_os.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()
    with TestClient(app_mod.app) as c:
        yield c


def test_month_close_shape(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.get("/api/home/month-close")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("title") == "Close the month"
    assert body.get("total") >= 5
    assert len(body.get("steps") or []) >= 5
    ids = [s["id"] for s in body["steps"]]
    assert "fees" in ids
    assert "sort_charges" in ids
    assert "promo" in ids


def test_home_simple_has_month_close_and_promo(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={
            "cash_balance": 8000,
            "card_name": "0pct",
            "card_limit": 5000,
            "card_due_day": 15,
        },
    )
    r = client.get("/api/home/simple")
    body = r.json()
    assert "month_close" in body
    assert "promo_brief" in body
    assert body["month_close"].get("progress_label")


def test_mark_month_closed(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = client.get("/api/home/month-close")
    body = r.json()
    assert "closed_this_period" in body
    assert body.get("closed_this_period") is False
    recon = next(s for s in body["steps"] if s["id"] == "reconcile")
    # No bank bal yet — does not block (money-in habit not started)
    assert recon.get("done") is True

    # Force mark even if optional steps remain (test path)
    r2 = client.post("/api/home/month-close/complete", json={"force": True})
    assert r2.status_code == 200, r2.text
    done = r2.json()
    assert done.get("ok") is True
    assert done.get("period")
    assert done.get("checklist", {}).get("closed_this_period") is True

    r3 = client.get("/api/home/month-close")
    assert r3.json().get("closed_this_period") is True
    assert r3.json().get("can_mark_closed") is False


def test_month_close_gate_partial_bank_bal(tmp_path, monkeypatch):
    """After first bank bal, every cash IFPP account needs one."""
    from financial_os.db import Account, Profile
    from financial_os.services.month_close import build_month_close
    from financial_os.services.onboarding import apply_first_run
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        apply_first_run(
            s,
            cash_name="Primary checking",
            cash_balance=Decimal("1000"),
        )
        s.flush()
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        # Second cash without bank bal
        s.add(
            Account(
                profile_id=personal.id,
                kind="savings",
                nickname="Emergency",
                current_balance=Decimal("200"),
                is_cash_for_ifpp=True,
            )
        )
        primary = s.query(Account).filter(Account.nickname == "Primary checking").one()
        primary.institution_balance = Decimal("1000")
        s.flush()
        close = build_month_close(s, profile_id=personal.id)
        recon = next(x for x in close["steps"] if x["id"] == "reconcile")
        assert recon["done"] is False
        assert recon.get("missing_bank_balance", 1) >= 1 or "need bank" in recon["title"].lower()
    finally:
        s.close()
        eng.dispose()


def test_month_close_gate_cash_drift(tmp_path, monkeypatch):
    from financial_os.db import Account, Profile
    from financial_os.services.month_close import build_month_close
    from financial_os.services.onboarding import apply_first_run
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(eng)
    SF = sessionmaker(bind=eng)
    s = SF()
    try:
        seed_all(s)
        apply_first_run(s, cash_name="Primary checking", cash_balance=Decimal("100"))
        s.flush()
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        primary = s.query(Account).filter(Account.nickname == "Primary checking").one()
        primary.institution_balance = Decimal("500")
        s.flush()
        close = build_month_close(s, profile_id=personal.id)
        recon = next(x for x in close["steps"] if x["id"] == "reconcile")
        assert recon["done"] is False
        assert "drift" in recon["title"].lower()
        assert recon["action"] == "set_books_from_bank"
        assert recon.get("account_id") == primary.id
        assert "Safe to spend" in (recon.get("button_label") or "")
    finally:
        s.close()
        eng.dispose()
