"""Dynamic promo amortization calendar (multi-year financing)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.promo_installments import (
    create_promo_line,
    estimated_months_remaining,
    project_line_calendar,
    project_promo_calendar,
)


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


def test_estimated_months_24_mo_plan():
    # Home Depot–style 24 mo: 2400 / 100 = 24
    assert estimated_months_remaining(Decimal("2400"), Decimal("100")) == 24
    # Partial last month: 2500 / 100 = 25
    assert estimated_months_remaining(Decimal("2500"), Decimal("100")) == 25
    assert estimated_months_remaining(Decimal("0"), Decimal("100")) == 0


def test_project_line_calendar_24_months(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Home Depot",
        current_balance=Decimal("2400"),
        credit_limit=Decimal("10000"),
        payment_due_day=2,
        statement_close_day=7,
    )
    s.add(card)
    s.flush()
    line = create_promo_line(
        s,
        account_id=card.id,
        name="HD 24 mo 0%",
        principal_remaining=Decimal("2400"),
        monthly_payment=Decimal("100"),
        start_date=date(2026, 1, 1),
        end_date=date(2028, 12, 31),  # room for full 24 installments from Mar 2026
    )
    s.commit()

    cal = project_line_calendar(line, as_of=date(2026, 3, 15))
    assert cal["months_remaining"] == 24
    assert len(cal["months"]) == 24
    assert cal["months"][0]["payment"] == "100.00"
    assert cal["months"][0]["principal_after"] == "2300.00"
    assert cal["months"][-1]["payment"] == "100.00"
    assert cal["months"][-1]["principal_after"] == "0.00"
    assert cal["months"][-1]["is_final"] is True
    assert cal["capped"] is False
    assert cal["final_month"] is not None


def test_project_promo_calendar_merges_and_spans_longest(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="HD",
        current_balance=Decimal("3000"),
        credit_limit=Decimal("10000"),
    )
    s.add(card)
    s.flush()
    create_promo_line(
        s,
        account_id=card.id,
        name="24 mo",
        principal_remaining=Decimal("2400"),
        monthly_payment=Decimal("100"),
        start_date=date(2026, 1, 1),
    )
    create_promo_line(
        s,
        account_id=card.id,
        name="6 mo small",
        principal_remaining=Decimal("600"),
        monthly_payment=Decimal("100"),
        start_date=date(2026, 1, 1),
    )
    s.commit()

    cal = project_promo_calendar(s, card.id, as_of=date(2026, 1, 10))
    assert cal["line_count"] == 2
    assert cal["months_span"] == 24  # longest line
    assert len(cal["by_month"]) == 24
    # First month: both lines pay 100 → 200 total
    assert cal["by_month"][0]["payment_total"] == "200.00"
    # After month 6, only 24 mo line remains
    assert cal["by_month"][6]["payment_total"] == "100.00"
    assert cal["by_month"][6]["line_count"] == 1


def test_api_promo_calendar(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from financial_os.db import Account, Profile, init_db, make_engine, make_session_factory

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
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        card = Account(
            profile_id=p.id,
            kind="credit",
            nickname="HD",
            current_balance=Decimal("1200"),
            credit_limit=Decimal("5000"),
        )
        s.add(card)
        s.flush()
        create_promo_line(
            s,
            account_id=card.id,
            name="12 mo",
            principal_remaining=Decimal("1200"),
            monthly_payment=Decimal("100"),
            start_date=date(2026, 1, 1),
        )
        card_id = card.id
        s.commit()

    with TestClient(app_mod.app) as client:
        r = client.get(f"/api/accounts/{card_id}/promo-calendar")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["months_span"] == 12
        assert len(body["by_month"]) == 12
        assert body["lines"][0]["months_remaining"] == 12
