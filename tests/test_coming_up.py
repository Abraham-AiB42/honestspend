"""Coming up strip: window resolution (calendar / paydays) + cash-side list."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, ScheduledItem, init_db, make_engine, make_session_factory
from honestspend.migrations import SCHEMA_VERSION, get_schema_version
from honestspend.seed import seed_all
from honestspend.services.coming_up import build_coming_up, resolve_window
from honestspend.services.home_simple import build_home_simple


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

    import honestspend.api.app as app_mod

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


def test_calendar_mode_forces_calendar_even_with_income(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Paycheck",
            amount=Decimal("2000"),
            next_date=as_of + timedelta(days=3),
            cadence="biweekly",
            kind="income",
            active=True,
        )
    )
    s.commit()
    w = resolve_window(s, as_of=as_of, mode="calendar", calendar_days=7, profile_id=p.id)
    assert w["mode_effective"] == "calendar"
    assert w["window_end"] == (as_of + timedelta(days=7)).isoformat()
    s.close()


def test_paydays_no_income_falls_back(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    w = resolve_window(s, as_of=as_of, mode="paydays", profile_id=p.id)
    assert w["mode_effective"] == "calendar"
    assert w["window_fallback"] == "no_income"
    assert "no paycheck" in w["label"].lower()
    s.close()


def test_calendar_days_clamped(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    w_lo = resolve_window(s, as_of=as_of, mode="calendar", calendar_days=5, profile_id=p.id)
    w_hi = resolve_window(s, as_of=as_of, mode="calendar", calendar_days=30, profile_id=p.id)
    assert w_lo["calendar_days"] == 7
    assert w_hi["calendar_days"] == 14
    s.close()


def test_empty_hint_when_no_schedules(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    # Deactivate seed schedules if any
    for row in s.query(ScheduledItem).all():
        row.active = False
    s.commit()
    r = build_coming_up(s, as_of=date(2026, 3, 1), mode="calendar", profile_id=p.id)
    assert r["items"] == []
    assert r["count"] == 0
    assert r["empty_hint"]
    assert "Nothing scheduled" in r["empty_hint"]
    s.close()


def test_income_only_outflow_zero(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    for row in s.query(ScheduledItem).all():
        row.active = False
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Paycheck",
            amount=Decimal("2500"),
            next_date=as_of + timedelta(days=2),
            cadence="biweekly",
            kind="income",
            active=True,
        )
    )
    s.commit()
    r = build_coming_up(
        s, as_of=as_of, mode="calendar", calendar_days=14, profile_id=p.id, show_income=True
    )
    assert r["outflow_total"] == "0.00"
    assert Decimal(r["inflow_total"]) == Decimal("2500.00")
    assert any(i["direction"] == "in" for i in r["items"])
    s.close()


def test_totals_over_full_window_when_truncated(tmp_path: Path, monkeypatch):
    """outflow_total includes all window rows, not just top 8 list items."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    for row in s.query(ScheduledItem).all():
        row.active = False
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("10000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    # 10 expenses of $10 on different days
    for i in range(10):
        s.add(
            ScheduledItem(
                profile_id=p.id,
                name=f"Bill{i}",
                amount=Decimal("-10"),
                next_date=as_of + timedelta(days=i + 1),
                cadence="monthly",
                kind="expense",
                account_id=cash.id,
                active=True,
            )
        )
    s.commit()
    r = build_coming_up(
        s,
        as_of=as_of,
        mode="calendar",
        calendar_days=14,
        profile_id=p.id,
        limit=8,
        show_income=False,
    )
    assert r["truncated"] is True
    assert r["count"] == 10
    assert r["shown_count"] == 8
    assert len(r["items"]) == 8
    assert r["outflow_total"] == "100.00"  # full window 10×10
    s.close()


def test_window_capped_when_second_payday_missing(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    # Monthly income only once in 45d from next_date
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Monthly pay",
            amount=Decimal("3000"),
            next_date=as_of + timedelta(days=40),
            cadence="monthly",
            kind="income",
            active=True,
        )
    )
    s.commit()
    w = resolve_window(
        s, as_of=as_of, mode="paydays", payday_count=2, profile_id=p.id
    )
    assert w["window_capped"] is True
    assert w["window_end"] == (as_of + timedelta(days=45)).isoformat()
    # Plain English — no engine "horizon" jargon in user labels
    assert "horizon" not in (w.get("label") or "").lower()
    assert "few weeks" in (w.get("label") or "").lower() or "payday" in (w.get("label") or "").lower()
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


def test_migration_coming_up_settings_columns(tmp_path: Path, monkeypatch):
    """SCHEMA_VERSION >= 21 and app_settings has coming_up_* columns with defaults."""
    from sqlalchemy import inspect

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    eng = create_engine(f"sqlite:///{(data / 'mig.db').as_posix()}")
    init_db(eng)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 21

    for name in (
        "coming_up_window_mode",
        "coming_up_calendar_days",
        "coming_up_payday_count",
        "coming_up_show_income",
    ):
        assert hasattr(AppSettings, name), f"AppSettings missing {name}"

    insp = inspect(eng)
    cols = {c["name"]: c for c in insp.get_columns("app_settings")}
    for name in (
        "coming_up_window_mode",
        "coming_up_calendar_days",
        "coming_up_payday_count",
        "coming_up_show_income",
    ):
        assert name in cols, f"app_settings missing column {name}"

    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        seed_all(s)
        s.commit()
        row = s.get(AppSettings, 1)
        assert row is not None
        assert (row.coming_up_window_mode or "auto") == "auto"
        assert int(row.coming_up_calendar_days or 14) == 14
        assert int(row.coming_up_payday_count or 1) == 1
        assert bool(row.coming_up_show_income) is True
    finally:
        s.close()


def test_build_coming_up_reads_app_settings(tmp_path: Path, monkeypatch):
    """When mode not passed, build_coming_up uses AppSettings coming_up_* prefs."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    # Income exists — auto would use paydays; force calendar via settings
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
    st = s.get(AppSettings, 1)
    assert st is not None
    st.coming_up_window_mode = "calendar"
    st.coming_up_calendar_days = 7
    st.coming_up_show_income = False
    s.commit()

    # No mode / calendar_days / show_income → settings
    result = build_coming_up(s, as_of=as_of, profile_id=p.id)
    assert result["window"]["mode_effective"] == "calendar"
    assert result["window"]["calendar_days"] == 7
    assert result["window"]["window_end"] == (as_of + timedelta(days=7)).isoformat()
    # Income hidden via settings
    assert all(i["direction"] != "in" for i in result["items"])

    # Explicit override wins over settings
    result2 = build_coming_up(
        s,
        as_of=as_of,
        profile_id=p.id,
        mode="auto",
        show_income=True,
    )
    assert result2["window"]["mode_effective"] == "paydays"
    assert result2["window"]["window_end"] == payday.isoformat()
    s.close()


def test_home_simple_reads_coming_up_settings(tmp_path: Path, monkeypatch):
    """home_simple coming_up embeds settings-driven window when no query overrides."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    st = s.get(AppSettings, 1)
    assert st is not None
    st.coming_up_window_mode = "calendar"
    st.coming_up_calendar_days = 10
    s.commit()

    home = build_home_simple(s, profile_id=p.id, as_of=as_of)
    cu = home["coming_up"]
    assert cu["window"]["mode_effective"] == "calendar"
    assert cu["window"]["calendar_days"] == 10
    assert cu["window"]["window_end"] == (as_of + timedelta(days=10)).isoformat()
    s.close()


def test_patch_coming_up_settings(api_client: TestClient):
    """PATCH /api/settings accepts coming_up_* and home/coming-up honor them."""
    api_client.post("/api/onboarding/quick-setup", json={"cash_balance": 8000})
    r = api_client.patch(
        "/api/settings",
        json={
            "coming_up_window_mode": "calendar",
            "coming_up_calendar_days": 7,
            "coming_up_payday_count": 2,
            "coming_up_show_income": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coming_up_window_mode"] == "calendar"
    assert body["coming_up_calendar_days"] == 7
    assert body["coming_up_payday_count"] == 2
    assert body["coming_up_show_income"] is False

    r2 = api_client.get("/api/settings")
    assert r2.json()["coming_up_window_mode"] == "calendar"

    r3 = api_client.get("/api/coming-up")
    assert r3.status_code == 200, r3.text
    assert r3.json()["window"]["mode_effective"] == "calendar"
    assert r3.json()["window"]["calendar_days"] == 7

    # Invalid mode rejected
    bad = api_client.patch("/api/settings", json={"coming_up_window_mode": "nope"})
    assert bad.status_code == 400


def test_owner_draw_is_outflow_not_income(tmp_path: Path, monkeypatch):
    """kind=owner_draw with negative amount → direction out; never payday income."""
    from honestspend.services.coming_up import is_income_schedule, next_income_dates

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("10000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    draw = ScheduledItem(
        profile_id=p.id,
        account_id=cash.id,
        name="Owner draw",
        amount=Decimal("-2500"),
        next_date=as_of + timedelta(days=2),
        start_date=as_of,
        cadence="monthly",
        certainty="fixed",
        kind="owner_draw",
        active=True,
    )
    s.add(draw)
    s.commit()

    assert is_income_schedule(draw) is False
    # Positive amount still never income when kind is owner_draw
    draw.amount = Decimal("2500")
    assert is_income_schedule(draw) is False
    draw.amount = Decimal("-2500")

    paydays = next_income_dates(
        s, as_of=as_of, profile_id=p.id, scope="entity", count=2
    )
    assert paydays == []

    result = build_coming_up(
        s,
        as_of=as_of,
        mode="calendar",
        calendar_days=14,
        profile_id=p.id,
        show_income=True,
    )
    names = [i["name"] for i in result["items"]]
    assert "Owner draw" in names
    row = next(i for i in result["items"] if i["name"] == "Owner draw")
    assert row["direction"] == "out"
    assert row["kind"] == "owner_draw"
    assert Decimal(row["amount"]) < 0
    assert Decimal(result["outflow_total"]) >= Decimal("2500.00")
    assert Decimal(result["inflow_total"]) == Decimal("0.00")
    s.close()


def test_coming_up_respects_schedule_start_date(tmp_path: Path, monkeypatch):
    """Schedule with start_date after as_of: early next_date occurrences skipped."""
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
    s.add(
        ScheduledItem(
            profile_id=p.id,
            account_id=cash.id,
            name="Deferred rent",
            amount=Decimal("-1100"),
            next_date=as_of,  # would fire Mar 1 without start_date
            start_date=date(2026, 4, 1),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    s.commit()

    # Window covers Mar only — occurrence blocked by start_date
    r_mar = build_coming_up(
        s,
        as_of=as_of,
        mode="calendar",
        calendar_days=14,
        profile_id=p.id,
        show_income=False,
    )
    assert not any(i["name"] == "Deferred rent" for i in r_mar["items"])

    # Window in April includes it
    r_apr = build_coming_up(
        s,
        as_of=date(2026, 4, 1),
        mode="calendar",
        calendar_days=14,
        profile_id=p.id,
        show_income=False,
    )
    names = [i["name"] for i in r_apr["items"]]
    assert "Deferred rent" in names
    s.close()
