"""Coming up strip: window resolution (calendar / paydays) + cash-side list."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, ScheduledItem, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.coming_up import build_coming_up, resolve_window


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
def api_client(tmp_path, monkeypatch):
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


def test_resolve_window_calendar_14(tmp_path: Path, monkeypatch):
    """No income → auto uses calendar 14 days."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)

    w = resolve_window(s, as_of=as_of, mode="auto", profile_id=p.id)

    assert w["mode_effective"] == "calendar"
    assert w["window_start"] == as_of.isoformat()
    assert w["window_end"] == (as_of + timedelta(days=14)).isoformat()
    assert w["calendar_days"] == 14
    assert w["payday_count"] is None
    assert w["paydays"] == []
    assert w["label"] == "Next 14 days"
    assert w["window_fallback"] is None
    assert w["window_capped"] is False
    s.close()


def test_resolve_window_next_payday(tmp_path: Path, monkeypatch):
    """Biweekly income next_date = as_of+5 → window_end that date, mode paydays."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    payday = as_of + timedelta(days=5)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Paycheck",
            amount=Decimal("2100"),
            next_date=payday,
            cadence="biweekly",
            certainty="fixed",
            kind="income",
            active=True,
        )
    )
    s.commit()

    w = resolve_window(s, as_of=as_of, mode="auto", profile_id=p.id)

    assert w["mode_effective"] == "paydays"
    assert w["window_start"] == as_of.isoformat()
    assert w["window_end"] == payday.isoformat()
    assert w["calendar_days"] is None
    assert w["payday_count"] == 1
    assert w["paydays"] == [payday.isoformat()]
    assert w["window_fallback"] is None
    assert w["window_capped"] is False
    # Until Fri Mar 6 (next payday) — 2026-03-06 is Friday
    assert "next payday" in w["label"]
    assert payday.strftime("%a") in w["label"]
    assert "Mar" in w["label"]
    assert "6" in w["label"]
    s.close()


def test_resolve_window_two_paydays(tmp_path: Path, monkeypatch):
    """Biweekly → 2nd occurrence ~14 days after first."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    first = as_of + timedelta(days=5)
    second = first + timedelta(days=14)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Paycheck",
            amount=Decimal("2100"),
            next_date=first,
            cadence="biweekly",
            certainty="fixed",
            kind="income",
            active=True,
        )
    )
    s.commit()

    w = resolve_window(
        s,
        as_of=as_of,
        mode="paydays",
        payday_count=2,
        profile_id=p.id,
    )

    assert w["mode_effective"] == "paydays"
    assert w["window_end"] == second.isoformat()
    assert w["payday_count"] == 2
    assert w["paydays"] == [first.isoformat(), second.isoformat()]
    assert "2nd payday" in w["label"]
    s.close()


def test_coming_up_includes_card_payment_on_cash_skips_credit_netflix(
    tmp_path: Path, monkeypatch
):
    """Card payment · Visa on checking included; Netflix on credit skipped."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("400"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4600"),
    )
    s.add_all([cash, card])
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Card payment · Visa",
            amount=Decimal("-340"),
            next_date=as_of + timedelta(days=3),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Netflix",
            amount=Decimal("-15.99"),
            next_date=as_of + timedelta(days=2),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=card.id,
            active=True,
        )
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
    assert "Card payment · Visa" in names
    assert "Netflix" not in names
    card_row = next(i for i in result["items"] if i["name"].startswith("Card payment"))
    assert card_row["is_card_payment"] is True
    assert card_row["direction"] == "out"
    assert card_row["amount"] == "-340.00"
    assert Decimal(result["outflow_total"]) == Decimal("340.00")
    s.close()


def test_coming_up_sorts_by_date(tmp_path: Path, monkeypatch):
    """Items sorted by on_date ascending, then larger abs amount first."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    later = as_of + timedelta(days=10)
    earlier = as_of + timedelta(days=2)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Rent",
            amount=Decimal("-1600"),
            next_date=later,
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Internet",
            amount=Decimal("-80"),
            next_date=earlier,
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Gym",
            amount=Decimal("-40"),
            next_date=earlier,
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
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

    assert len(result["items"]) == 3
    assert result["items"][0]["name"] == "Internet"  # earlier, larger abs than Gym
    assert result["items"][1]["name"] == "Gym"
    assert result["items"][2]["name"] == "Rent"
    assert result["items"][0]["on_date"] == earlier.isoformat()
    assert result["items"][2]["on_date"] == later.isoformat()
    assert result["count"] == 3
    s.close()


def test_home_simple_includes_coming_up(api_client: TestClient):
    """Home payload embeds coming_up with items list."""
    api_client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = api_client.get("/api/home/simple")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "coming_up" in body
    cu = body["coming_up"]
    assert isinstance(cu.get("items"), list)
    assert "window" in cu
    assert "count" in cu


def test_api_coming_up_200(api_client: TestClient):
    """GET /api/coming-up returns 200 with full payload shape."""
    api_client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = api_client.get("/api/coming-up")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("items"), list)
    assert "window" in body
    assert body["window"].get("mode_effective") in ("auto", "calendar", "paydays")
    assert "outflow_total" in body
    assert "inflow_total" in body
    assert "count" in body

    r2 = api_client.get(
        "/api/coming-up",
        params={
            "mode": "calendar",
            "calendar_days": 14,
            "payday_count": 1,
            "limit": 8,
            "show_income": True,
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["window"]["mode_effective"] == "calendar"
