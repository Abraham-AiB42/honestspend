"""Promo installment lines: month roll + statement carve-out (ISB-class)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, PromoInstallmentLine, init_db, make_engine, make_session_factory
from financial_os.migrations import SCHEMA_VERSION, get_schema_version
from financial_os.seed import seed_all
from financial_os.services.promo_installments import (
    apply_month_roll,
    create_promo_line,
    open_promo_totals,
)
from financial_os.services.statement_cycle import project_card_payment


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


def test_schema_promo_installment_lines(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    init_db(eng)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 18
    assert "promo_installment_lines" in inspect(eng).get_table_names()
    cols = {c["name"] for c in inspect(eng).get_columns("promo_installment_lines")}
    for name in (
        "account_id",
        "name",
        "principal_remaining",
        "monthly_payment",
        "start_date",
        "end_date",
        "active",
        "source",
    ):
        assert name in cols, f"missing column {name}"


def test_open_promo_totals_and_month_roll(tmp_path: Path, monkeypatch):
    """ISB-style example: $823.78 principal / 12 months."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="ISB Card",
        current_balance=Decimal("5000.00"),
        credit_limit=Decimal("10000"),
        statement_close_day=18,
        payment_due_day=25,
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()

    principal = Decimal("823.78")
    monthly = (principal / Decimal("12")).quantize(Decimal("0.01"))  # 68.65
    as_of = date(2026, 9, 15)

    line = create_promo_line(
        s,
        card.id,
        name="Appliance plan",
        principal_remaining=principal,
        monthly_payment=monthly,
        start_date=date(2026, 1, 1),
        source="isb",
    )
    s.commit()

    rem, due = open_promo_totals(s, card.id, as_of=as_of)
    assert rem == Decimal("823.78")
    assert due == monthly == Decimal("68.65")

    apply_month_roll(s, card.id, as_of=as_of)
    s.commit()

    s.refresh(line)
    expected = (principal - monthly).quantize(Decimal("0.01"))
    assert line.principal_remaining == expected
    assert line.active is True

    rem2, due2 = open_promo_totals(s, card.id, as_of=as_of)
    assert rem2 == expected
    assert due2 == monthly

    s.close()


def test_statement_payment_carves_out_promo_line(tmp_path: Path, monkeypatch):
    """statement pay = max(0, balance - promo_remaining) + monthly_due."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("5000.00"),
        credit_limit=Decimal("10000"),
        statement_close_day=18,
        payment_due_day=25,
        autopay_policy="statement",
        min_payment=Decimal("50"),
    )
    s.add(card)
    s.flush()

    principal = Decimal("823.78")
    monthly = (principal / Decimal("12")).quantize(Decimal("0.01"))  # 68.65
    create_promo_line(
        s,
        card.id,
        name="ISB plan",
        principal_remaining=principal,
        monthly_payment=monthly,
        start_date=date(2026, 1, 1),
        source="isb",
    )
    s.commit()

    as_of = date(2026, 9, 15)
    proj = project_card_payment(s, card.id, as_of=as_of)

    # statement_balance carves out full promo principal
    assert proj["statement_balance"] == Decimal("4176.22")  # 5000 - 823.78
    # next_payment = 4176.22 + 68.65
    assert proj["next_payment"] == Decimal("4244.87")
    assert proj["promo_remaining"] == Decimal("823.78")
    assert proj["promo_due"] == Decimal("68.65")

    s.close()


def test_month_roll_deactivates_when_zero(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Card",
        current_balance=Decimal("100"),
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    line = create_promo_line(
        s,
        card.id,
        name="Last payment",
        principal_remaining=Decimal("50.00"),
        monthly_payment=Decimal("50.00"),
        start_date=date(2026, 1, 1),
    )
    s.commit()

    apply_month_roll(s, card.id, as_of=date(2026, 9, 1))
    s.commit()
    s.refresh(line)
    assert line.principal_remaining == Decimal("0.00")
    assert line.active is False

    rem, due = open_promo_totals(s, card.id, as_of=date(2026, 9, 1))
    assert rem == Decimal("0.00")
    assert due == Decimal("0.00")
    s.close()


def test_future_start_excluded_from_open_totals(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Card",
        current_balance=Decimal("1000"),
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    create_promo_line(
        s,
        card.id,
        name="Future plan",
        principal_remaining=Decimal("200"),
        monthly_payment=Decimal("20"),
        start_date=date(2027, 1, 1),
    )
    s.commit()
    rem, due = open_promo_totals(s, card.id, as_of=date(2026, 9, 15))
    assert rem == Decimal("0.00")
    assert due == Decimal("0.00")
    s.close()


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


def _seed_credit(app_mod) -> int:
    with app_mod.SessionLocal() as s:
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="API Visa",
            current_balance=Decimal("5000.00"),
            credit_limit=Decimal("10000"),
            statement_close_day=18,
            payment_due_day=25,
            autopay_policy="statement",
        )
        s.add(card)
        s.commit()
        return card.id


def test_api_promo_lines_crud_and_roll(client: TestClient):
    import financial_os.api.app as app_mod

    card_id = _seed_credit(app_mod)

    r = client.get(f"/api/accounts/{card_id}/promo-lines")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0

    r = client.post(
        f"/api/accounts/{card_id}/promo-lines",
        json={
            "name": "ISB appliance",
            "principal_remaining": "823.78",
            "monthly_payment": "68.65",
            "start_date": "2026-01-01",
            "source": "isb",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "ISB appliance"
    assert Decimal(body["principal_remaining"]) == Decimal("823.78")
    assert Decimal(body["monthly_payment"]) == Decimal("68.65")
    assert body["active"] is True
    line_id = body["id"]

    r = client.get(f"/api/accounts/{card_id}/promo-lines")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # Cycle projection reflects carve-out
    r = client.get(f"/api/accounts/{card_id}/cycle")
    assert r.status_code == 200
    cycle = r.json()
    assert Decimal(str(cycle["statement_balance"])) == Decimal("4176.22")
    assert Decimal(str(cycle["next_payment"])) == Decimal("4244.87")

    r = client.post(f"/api/accounts/{card_id}/promo-lines/{line_id}/roll")
    assert r.status_code == 200, r.text
    rolled = r.json()
    assert Decimal(rolled["principal_remaining"]) == Decimal("755.13")  # 823.78 - 68.65
    assert rolled["active"] is True


def test_api_promo_line_not_found(client: TestClient):
    import financial_os.api.app as app_mod

    card_id = _seed_credit(app_mod)
    r = client.post(f"/api/accounts/{card_id}/promo-lines/999999/roll")
    assert r.status_code == 404
