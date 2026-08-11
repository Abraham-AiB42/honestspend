"""HTTP API: schedule agency fields + series step/list."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from financial_os.api.app import ScheduledIn
from financial_os.config import settings
from financial_os.db import Account, Profile, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all


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


def _personal_and_cash(app_mod) -> tuple[int, int]:
    with app_mod.SessionLocal() as s:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="Agency cash",
            current_balance=Decimal("10000"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.commit()
        return p.id, cash.id


def test_scheduled_in_accepts_agency_fields_and_owner_draw():
    body = ScheduledIn(
        profile_id=1,
        name="Owner draw",
        amount=Decimal("2500"),
        next_date=date(2026, 8, 15),
        start_date=date(2026, 8, 1),
        kind="owner_draw",
        account_id=3,
        series_id="abc123",
        series_label="Owner draws 2026",
        vendor=None,
        opex_class=None,
        income_source=None,
    )
    assert body.kind == "owner_draw"
    assert body.amount == Decimal("-2500")
    assert body.series_id == "abc123"
    assert body.start_date == date(2026, 8, 1)

    rent = ScheduledIn(
        profile_id=1,
        name="Office rent",
        amount=Decimal("-2000"),
        next_date=date(2026, 5, 1),
        start_date=date(2026, 4, 16),
        kind="expense",
        account_id=3,
        series_id="rent-1",
        series_label="Office rent",
        vendor="Acme Properties",
        opex_class="fixed",
    )
    assert rent.vendor == "Acme Properties"
    assert rent.opex_class == "fixed"

    inc = ScheduledIn(
        profile_id=1,
        name="Retainers",
        amount=Decimal("8000"),
        next_date=date(2026, 8, 1),
        kind="income",
        income_source="retainers",
    )
    assert inc.income_source == "retainers"
    assert inc.amount == Decimal("8000")


def test_owner_draw_requires_account():
    with pytest.raises(ValidationError):
        ScheduledIn(
            profile_id=1,
            name="Draw",
            amount=Decimal("100"),
            next_date=date(2026, 8, 1),
            kind="owner_draw",
            account_id=None,
        )


def test_create_scheduled_with_agency_fields(client: TestClient):
    import financial_os.api.app as app_mod

    pid, cash_id = _personal_and_cash(app_mod)
    series = "rent-series-api-001"
    r = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Office rent",
            "amount": "-2000.00",
            "next_date": "2026-05-01",
            "start_date": "2026-04-16",
            "cadence": "monthly",
            "kind": "expense",
            "account_id": cash_id,
            "series_id": series,
            "series_label": "Office rent",
            "vendor": "Acme Properties",
            "opex_class": "fixed",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["series_id"] == series
    assert body["series_label"] == "Office rent"
    assert body["vendor"] == "Acme Properties"
    assert body["opex_class"] == "fixed"
    assert body["start_date"] == "2026-04-16"
    assert Decimal(str(body["amount"])) == Decimal("-2000.00")
    assert body["kind"] == "expense"

    r2 = client.get(f"/api/scheduled/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["vendor"] == "Acme Properties"


def test_create_owner_draw_via_api(client: TestClient):
    import financial_os.api.app as app_mod

    pid, cash_id = _personal_and_cash(app_mod)
    r = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Owner draw",
            "amount": "2500",
            "next_date": "2026-08-15",
            "start_date": "2026-08-01",
            "kind": "owner_draw",
            "account_id": cash_id,
            "series_id": "draw-series-1",
            "series_label": "Owner draws 2026",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "owner_draw"
    assert Decimal(str(body["amount"])) == Decimal("-2500.00")
    assert body["series_id"] == "draw-series-1"


def test_series_step_and_list(client: TestClient):
    import financial_os.api.app as app_mod

    pid, cash_id = _personal_and_cash(app_mod)
    series = "rent-step-series"
    r = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Office rent",
            "amount": "-2000",
            "next_date": "2026-05-01",
            "start_date": "2026-04-16",
            "cadence": "monthly",
            "kind": "expense",
            "account_id": cash_id,
            "series_id": series,
            "series_label": "Office rent",
            "vendor": "Acme Properties",
            "opex_class": "fixed",
        },
    )
    assert r.status_code == 200, r.text
    first_id = r.json()["id"]

    step = client.post(
        "/api/scheduled/series/step",
        json={
            "series_id": series,
            "new_amount": "-2100",
            "effective_from": "2026-07-29",
        },
    )
    assert step.status_code == 200, step.text
    second = step.json()
    assert second["series_id"] == series
    assert second["series_label"] == "Office rent"
    assert second["vendor"] == "Acme Properties"
    assert second["opex_class"] == "fixed"
    assert second["start_date"] == "2026-07-29"
    assert Decimal(str(second["amount"])) == Decimal("-2100.00")
    assert second["id"] != first_id

    # Previous segment closed day before step
    first = client.get(f"/api/scheduled/{first_id}")
    assert first.status_code == 200
    assert first.json()["end_date"] == "2026-07-28"

    listed = client.get("/api/scheduled/series", params={"profile_id": pid})
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    by_sid = {x["series_id"]: x for x in rows}
    assert series in by_sid
    rent = by_sid[series]
    assert rent["label"] == "Office rent"
    assert rent["profile_id"] == pid
    assert len(rent["segments"]) == 2
    amts = [Decimal(str(seg["amount"])) for seg in rent["segments"]]
    assert Decimal("-2000") in amts or Decimal("-2000.00") in amts
    assert any(Decimal(str(a)) == Decimal("-2100") for a in amts)


def test_series_step_unknown_404(client: TestClient):
    r = client.post(
        "/api/scheduled/series/step",
        json={
            "series_id": "does-not-exist",
            "new_amount": "-100",
            "effective_from": "2026-08-01",
        },
    )
    assert r.status_code == 404


def test_list_series_requires_profile(client: TestClient):
    r = client.get("/api/scheduled/series")
    assert r.status_code == 422


def test_list_series_profile_not_found(client: TestClient):
    r = client.get("/api/scheduled/series", params={"profile_id": 999999})
    assert r.status_code == 404


def test_list_scheduled_filter_opex_class_and_income_source(client: TestClient):
    """Create with opex_class / income_source; list filters return only matches."""
    import financial_os.api.app as app_mod

    pid, cash_id = _personal_and_cash(app_mod)

    fixed = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Office rent fixed",
            "amount": "-2000",
            "next_date": "2026-05-01",
            "cadence": "monthly",
            "kind": "expense",
            "account_id": cash_id,
            "opex_class": "fixed",
        },
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["opex_class"] == "fixed"
    fixed_id = fixed.json()["id"]

    variable = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Ads variable",
            "amount": "-500",
            "next_date": "2026-05-02",
            "cadence": "monthly",
            "kind": "expense",
            "account_id": cash_id,
            "opex_class": "variable",
        },
    )
    assert variable.status_code == 200, variable.text
    variable_id = variable.json()["id"]

    income = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "e-Folio commissions",
            "amount": "3000",
            "next_date": "2026-05-15",
            "cadence": "monthly",
            "kind": "income",
            "account_id": cash_id,
            "income_source": "farmers_efolio",
        },
    )
    assert income.status_code == 200, income.text
    assert income.json()["income_source"] == "farmers_efolio"
    income_id = income.json()["id"]

    other_income = client.post(
        "/api/scheduled",
        json={
            "profile_id": pid,
            "name": "Other retainers",
            "amount": "1000",
            "next_date": "2026-05-20",
            "cadence": "monthly",
            "kind": "income",
            "account_id": cash_id,
            "income_source": "retainers",
        },
    )
    assert other_income.status_code == 200, other_income.text

    fixed_list = client.get(
        "/api/scheduled",
        params={"profile_id": pid, "opex_class": "fixed"},
    )
    assert fixed_list.status_code == 200, fixed_list.text
    fixed_ids = {row["id"] for row in fixed_list.json()}
    assert fixed_id in fixed_ids
    assert variable_id not in fixed_ids
    assert all(row.get("opex_class") == "fixed" for row in fixed_list.json())

    var_list = client.get(
        "/api/scheduled",
        params={"profile_id": pid, "opex_class": "variable"},
    )
    assert var_list.status_code == 200
    var_ids = {row["id"] for row in var_list.json()}
    assert variable_id in var_ids
    assert fixed_id not in var_ids

    efolio = client.get(
        "/api/scheduled",
        params={"profile_id": pid, "income_source": "farmers_efolio"},
    )
    assert efolio.status_code == 200, efolio.text
    efolio_ids = {row["id"] for row in efolio.json()}
    assert income_id in efolio_ids
    assert all(row.get("income_source") == "farmers_efolio" for row in efolio.json())
    assert other_income.json()["id"] not in efolio_ids

    bad = client.get("/api/scheduled", params={"opex_class": "not-a-class"})
    assert bad.status_code == 400
