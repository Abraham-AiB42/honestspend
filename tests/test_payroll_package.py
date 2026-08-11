"""Payroll package: net + employer tax schedules; coming_up lists both."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, ScheduledItem, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.coming_up import build_coming_up
from financial_os.services.payroll_package import create_payroll_package


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


def test_create_payroll_package_two_items(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Biz ops",
        current_balance=Decimal("50000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    pay = date(2026, 8, 15)
    result = create_payroll_package(
        s,
        profile_id=p.id,
        name="Biweekly payroll",
        pay_date=pay,
        cadence="biweekly",
        net_payroll=Decimal("8500"),
        employer_tax=Decimal("1200"),
        cash_account_id=cash.id,
        series_label="Payroll 2026",
    )
    s.commit()

    assert "package_id" in result
    assert len(result["package_id"]) == 32
    assert result["net_id"] != result["tax_id"]

    net = s.get(ScheduledItem, result["net_id"])
    tax = s.get(ScheduledItem, result["tax_id"])
    assert net is not None and tax is not None
    assert net.name == "Biweekly payroll · net"
    assert tax.name == "Biweekly payroll · employer tax"
    assert Decimal(str(net.amount)) == Decimal("-8500")
    assert Decimal(str(tax.amount)) == Decimal("-1200")
    assert net.next_date == pay
    assert tax.next_date == pay
    assert net.cadence == tax.cadence == "biweekly"
    assert net.account_id == tax.account_id == cash.id
    assert net.kind == tax.kind == "expense"
    assert net.active and tax.active
    assert f"package_id={result['package_id']}" in (net.notes or "")
    assert f"package_id={result['package_id']}" in (tax.notes or "")
    assert net.series_label == "Payroll 2026 · net"
    assert tax.series_label == "Payroll 2026 · employer tax"
    # Independent series so amount steps work per leg
    assert result.get("net_series_id")
    assert result.get("tax_series_id")
    assert result["net_series_id"] != result["tax_series_id"]
    assert net.series_id == result["net_series_id"]
    assert tax.series_id == result["tax_series_id"]
    s.close()


def test_payroll_package_rejects_credit_funding(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Amex",
        current_balance=Decimal("100"),
        credit_limit=Decimal("5000"),
    )
    s.add(card)
    s.flush()
    with pytest.raises(ValueError, match="cash-like"):
        create_payroll_package(
            s,
            profile_id=p.id,
            name="Payroll",
            pay_date=date(2026, 8, 15),
            cadence="biweekly",
            net_payroll=Decimal("1000"),
            employer_tax=Decimal("100"),
            cash_account_id=card.id,
        )
    s.close()


def test_payroll_package_in_coming_up(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("20000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()

    as_of = date(2026, 8, 1)
    pay = date(2026, 8, 7)
    create_payroll_package(
        s,
        profile_id=p.id,
        name="Biweekly payroll",
        pay_date=pay,
        cadence="biweekly",
        net_payroll=Decimal("5000"),
        employer_tax=Decimal("750"),
        cash_account_id=cash.id,
    )
    s.commit()

    result = build_coming_up(
        s,
        as_of=as_of,
        mode="calendar",
        calendar_days=14,
        profile_id=p.id,
        show_income=False,
    )
    names = [i["name"] for i in result["items"]]
    assert "Biweekly payroll · net" in names
    assert "Biweekly payroll · employer tax" in names

    net_row = next(i for i in result["items"] if i["name"].endswith("· net"))
    tax_row = next(i for i in result["items"] if "employer tax" in i["name"])
    assert net_row["on_date"] == pay.isoformat()
    assert tax_row["on_date"] == pay.isoformat()
    assert net_row["direction"] == "out"
    assert tax_row["direction"] == "out"
    assert Decimal(str(net_row["amount"])) == Decimal("-5000.00")
    assert Decimal(str(tax_row["amount"])) == Decimal("-750.00")
    s.close()


def test_api_payroll_packages(client: TestClient):
    import financial_os.api.app as app_mod

    with app_mod.SessionLocal() as s:
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="Payroll cash",
            current_balance=Decimal("30000"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.commit()
        pid, cash_id = p.id, cash.id

    r = client.post(
        "/api/payroll-packages",
        json={
            "profile_id": pid,
            "name": "Semi-monthly payroll",
            "pay_date": "2026-09-01",
            "cadence": "semimonthly",
            "net_payroll": "4200.00",
            "employer_tax": "640.50",
            "cash_account_id": cash_id,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["package_id"]
    assert body["net_id"]
    assert body["tax_id"]

    r_net = client.get(f"/api/scheduled/{body['net_id']}")
    r_tax = client.get(f"/api/scheduled/{body['tax_id']}")
    assert r_net.status_code == 200
    assert r_tax.status_code == 200
    assert r_net.json()["name"] == "Semi-monthly payroll · net"
    assert r_tax.json()["name"] == "Semi-monthly payroll · employer tax"
    assert Decimal(str(r_net.json()["amount"])) == Decimal("-4200.00")
    assert Decimal(str(r_tax.json()["amount"])) == Decimal("-640.50")
    assert f"package_id={body['package_id']}" in (r_net.json().get("notes") or "")
