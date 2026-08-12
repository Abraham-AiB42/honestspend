"""Safe until next paycheck / end of Coming up window — soft Home line."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, AppSettings, Profile, ScheduledItem, init_db
from honestspend.seed import seed_all
from honestspend.services.cash_runway import build_cash_runway, build_safe_until_window
from honestspend.services.coming_up import build_coming_up
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


def _zero_buffer(s) -> None:
    st = s.get(AppSettings, 1)
    if st:
        st.safety_buffer = Decimal("0")
        st.tax_vault_enabled = False
        st.tax_vault_balance = Decimal("0")


def test_safe_until_window_start_minus_outflows_paydays(tmp_path: Path, monkeypatch):
    """amount = max(0, runway start − cash-side outflows) through next payday."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _zero_buffer(s)
    as_of = date(2026, 3, 1)
    payday = as_of + timedelta(days=5)  # Fri Mar 6
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
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
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Rent",
            amount=Decimal("-800"),
            next_date=as_of + timedelta(days=2),
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
            next_date=as_of + timedelta(days=4),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.commit()

    cu = build_coming_up(
        s,
        as_of=as_of,
        mode="auto",
        profile_id=p.id,
        show_income=False,
    )
    runway = build_cash_runway(s, as_of=as_of, days=30, profile_id=p.id)
    soft = build_safe_until_window(
        s, as_of=as_of, profile_id=p.id, coming_up=cu
    )

    assert cu["window"]["mode_effective"] == "paydays"
    assert cu["window"]["window_end"] == payday.isoformat()
    assert soft["window_end"] == payday.isoformat()
    assert soft["starting_cash"] == runway["starting_cash"] == "2000.00"
    assert soft["outflows"] == "840.00"
    assert soft["amount"] == "1160.00"  # 2000 - 840
    assert "next payday" in soft["label"]
    assert "Safe until" in soft["label"]
    assert "Mar" in soft["label"]
    s.close()


def test_safe_until_window_floors_at_zero(tmp_path: Path, monkeypatch):
    """When outflows exceed start, amount is 0 not negative."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _zero_buffer(s)
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Big bill",
            amount=Decimal("-500"),
            next_date=as_of + timedelta(days=3),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.commit()

    soft = build_safe_until_window(s, as_of=as_of, profile_id=p.id)
    assert soft["starting_cash"] == "100.00"
    assert soft["outflows"] == "500.00"
    assert soft["amount"] == "0.00"
    assert "next" in soft["label"].lower() or "Safe until" in soft["label"]
    s.close()


def test_safe_until_skips_credit_account_schedules(tmp_path: Path, monkeypatch):
    """Netflix on credit not in outflows; Card payment on cash is."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _zero_buffer(s)
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1500"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("200"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4800"),
    )
    s.add_all([cash, card])
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Card payment · Visa",
            amount=Decimal("-200"),
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

    soft = build_safe_until_window(
        s,
        as_of=as_of,
        profile_id=p.id,
        coming_up=build_coming_up(
            s, as_of=as_of, mode="calendar", calendar_days=14, profile_id=p.id
        ),
    )
    assert soft["outflows"] == "200.00"
    assert soft["amount"] == "1300.00"
    assert "next 14 days" in soft["label"]
    s.close()


def test_home_simple_includes_safe_until_window(tmp_path: Path, monkeypatch):
    """build_home_simple embeds safe_until_window; does not replace safe_to_spend."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _zero_buffer(s)
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("3000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    payday = as_of + timedelta(days=7)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Paycheck",
            amount=Decimal("2000"),
            next_date=payday,
            cadence="biweekly",
            certainty="fixed",
            kind="income",
            active=True,
        )
    )
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Utilities",
            amount=Decimal("-120"),
            next_date=as_of + timedelta(days=2),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=cash.id,
            active=True,
        )
    )
    s.commit()

    home = build_home_simple(s, profile_id=p.id, as_of=as_of)
    assert "safe_to_spend" in home
    assert "safe_until_window" in home
    su = home["safe_until_window"]
    assert set(su.keys()) >= {
        "amount",
        "window_end",
        "label",
        "starting_cash",
        "outflows",
    }
    assert su["window_end"] == payday.isoformat()
    assert su["outflows"] == "120.00"
    assert Decimal(su["amount"]) == Decimal(su["starting_cash"]) - Decimal(su["outflows"])
    # Soft line is not the same key as Safe to spend
    assert home["safe_to_spend"] != su  # types differ; ensure both present independently
    assert "next payday" in su["label"]
    s.close()
