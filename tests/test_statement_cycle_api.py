"""HTTP API: statement cycle projection + cycle-config settings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from honestspend.config import settings
from honestspend.db import Account, Profile, init_db, make_engine, make_session_factory
from honestspend.seed import seed_all


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import honestspend.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def _seed_card_and_cash(app_mod) -> tuple[int, int]:
    """Create checking + credit with cycle config; return (card_id, cash_id)."""
    with app_mod.SessionLocal() as s:
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops cash",
            current_balance=Decimal("5000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Cycle Visa",
            current_balance=Decimal("5000.00"),
            credit_limit=Decimal("10000"),
            statement_close_day=18,
            payment_due_day=25,
            autopay_policy="statement",
            min_payment=Decimal("50"),
            cycle_config_source="default",
        )
        s.add_all([cash, card])
        s.flush()
        card.payment_funding_account_id = cash.id
        s.commit()
        return card.id, cash.id


def test_get_cycle_projection(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, cash_id = _seed_card_and_cash(app_mod)

    r = client.get(f"/api/accounts/{card_id}/cycle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account_id"] == card_id
    assert body["policy"] == "statement"
    assert body["funding_account_id"] == cash_id
    assert Decimal(str(body["statement_balance"])) == Decimal("5000.00")
    assert Decimal(str(body["next_payment"])) == Decimal("5000.00")
    assert body["next_due"] is not None
    assert body["last_close"] is not None
    assert body["next_close"] is not None
    # dates are ISO strings
    date.fromisoformat(body["next_due"])
    date.fromisoformat(body["last_close"])
    date.fromisoformat(body["next_close"])


def test_get_cycle_not_found(client: TestClient):
    r = client.get("/api/accounts/999999/cycle")
    assert r.status_code == 404


def test_list_cycles(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, _cash_id = _seed_card_and_cash(app_mod)

    r = client.get("/api/accounts/cycles")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert body["count"] >= 1
    ids = {item["account_id"] for item in body["items"]}
    assert card_id in ids
    card_item = next(i for i in body["items"] if i["account_id"] == card_id)
    assert card_item["policy"] == "statement"
    assert "next_payment" in card_item
    assert "statement_balance" in card_item


def test_list_cycles_profile_filter(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, _ = _seed_card_and_cash(app_mod)
    with app_mod.SessionLocal() as s:
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        pid = personal.id

    r = client.get("/api/accounts/cycles", params={"profile_id": pid})
    assert r.status_code == 200
    ids = {i["account_id"] for i in r.json()["items"]}
    assert card_id in ids

    r2 = client.get("/api/accounts/cycles", params={"profile_id": 999999})
    assert r2.status_code == 200
    assert r2.json()["count"] == 0


def test_put_cycle_config_sets_user_source_and_recomputes(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, cash_id = _seed_card_and_cash(app_mod)

    r = client.put(
        f"/api/accounts/{card_id}/cycle-config",
        json={
            "statement_close_day": 10,
            "payment_due_day": 20,
            "autopay_policy": "min",
            "payment_funding_account_id": cash_id,
            "payment_fixed_amount": None,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True or body.get("cycle_config_source") == "user"
    assert body["cycle_config_source"] == "user"
    assert body["statement_close_day"] == 10
    assert body["payment_due_day"] == 20
    assert body["autopay_policy"] == "min" or body.get("policy") == "min"
    # recompute wrote caches / projection under min policy
    assert Decimal(str(body["next_payment"])) == Decimal("50.00")
    assert Decimal(str(body["statement_balance"])) == Decimal("5000.00")

    # Reverse-sync: autopay_policy min → payment_option minimum
    with app_mod.SessionLocal() as s:
        card = s.get(Account, card_id)
        assert card is not None
        assert card.autopay_policy == "min"
        assert card.payment_option == "minimum"

    # Persist check via GET
    g = client.get(f"/api/accounts/{card_id}/cycle")
    assert g.status_code == 200
    assert g.json()["policy"] == "min"
    assert g.json().get("cycle_config_source") == "user"
    assert Decimal(str(g.json()["next_payment"])) == Decimal("50.00")


def test_put_cycle_config_fixed_amount(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, cash_id = _seed_card_and_cash(app_mod)

    r = client.put(
        f"/api/accounts/{card_id}/cycle-config",
        json={
            "autopay_policy": "fixed",
            "payment_fixed_amount": "175.50",
            "payment_funding_account_id": cash_id,
            "payment_due_day": 15,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cycle_config_source"] == "user"
    assert Decimal(str(body["next_payment"])) == Decimal("175.50")


def test_put_cycle_config_not_credit(client: TestClient):
    import honestspend.api.app as app_mod

    with app_mod.SessionLocal() as s:
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Not a card",
            current_balance=Decimal("100"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.commit()
        cash_id = cash.id

    r = client.put(
        f"/api/accounts/{cash_id}/cycle-config",
        json={"autopay_policy": "min", "payment_due_day": 5},
    )
    assert r.status_code == 400


def test_put_cycle_config_rejects_credit_as_funding(client: TestClient):
    """Funding must be cash-like — another credit card is 400."""
    import honestspend.api.app as app_mod

    card_id, _cash_id = _seed_card_and_cash(app_mod)
    with app_mod.SessionLocal() as s:
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        other = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Other card",
            current_balance=Decimal("100"),
            credit_limit=Decimal("1000"),
        )
        s.add(other)
        s.commit()
        other_id = other.id

    r = client.put(
        f"/api/accounts/{card_id}/cycle-config",
        json={"payment_funding_account_id": other_id},
    )
    assert r.status_code == 400, r.text
    assert "cash" in r.json().get("detail", "").lower()


def test_post_recompute_cycle(client: TestClient):
    import honestspend.api.app as app_mod

    card_id, cash_id = _seed_card_and_cash(app_mod)

    r = client.post(f"/api/accounts/{card_id}/recompute-cycle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body["account_id"] == card_id
    assert Decimal(str(body["next_payment"])) == Decimal("5000.00")
    assert Decimal(str(body["statement_balance"])) == Decimal("5000.00")
    assert body["funding_account_id"] == cash_id
    # schedule upserted on funding cash when policy + amount active
    assert body.get("schedule") is not None


def test_put_cycle_config_clears_funding_with_null(client: TestClient):
    """Explicit null payment_funding_account_id clears server-side funding."""
    import honestspend.api.app as app_mod

    card_id, cash_id = _seed_card_and_cash(app_mod)
    r = client.put(
        f"/api/accounts/{card_id}/cycle-config",
        json={"payment_funding_account_id": cash_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["payment_funding_account_id"] == cash_id

    r = client.put(
        f"/api/accounts/{card_id}/cycle-config",
        json={"payment_funding_account_id": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["payment_funding_account_id"] is None
    with app_mod.SessionLocal() as s:
        card = s.get(Account, card_id)
        assert card.payment_funding_account_id is None


def test_list_accounts_includes_cycle_caches(client: TestClient):
    """AccountOut serializes statement/next-payment caches for AccountsPage."""
    import honestspend.api.app as app_mod

    card_id, _ = _seed_card_and_cash(app_mod)
    r = client.post(f"/api/accounts/{card_id}/recompute-cycle")
    assert r.status_code == 200, r.text

    r = client.get("/api/accounts")
    assert r.status_code == 200, r.text
    card = next(a for a in r.json() if a["id"] == card_id)
    assert "statement_balance_cached" in card
    assert "next_payment_amount_cached" in card
    assert "next_payment_date_cached" in card
    assert Decimal(str(card["statement_balance_cached"])) == Decimal("5000.00")
    assert Decimal(str(card["next_payment_amount_cached"])) == Decimal("5000.00")
    assert card.get("payment_funding_account_id") is not None


def test_post_recompute_cycle_not_found(client: TestClient):
    r = client.post("/api/accounts/999999/recompute-cycle")
    assert r.status_code == 404
