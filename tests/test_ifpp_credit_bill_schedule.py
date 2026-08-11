"""Card-paid bills must not double-count against cash Safe to spend."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Profile, ScheduledItem, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.recurring_detect import accept_recurring_suggestion, detect_recurring


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


def test_netflix_on_card_schedule_not_in_cash_runway(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    s.get(AppSettings, 1).safety_buffer = Decimal("0")
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=Decimal("100"),
        credit_limit=Decimal("5000"),
        available_credit=Decimal("4900"),
        payment_due_day=15,
        statement_close_day=1,
    )
    s.add_all([cash, card])
    s.flush()
    # Subscription billed on the card (not a cash outflow schedule)
    s.add(
        ScheduledItem(
            profile_id=p.id,
            name="Netflix",
            amount=Decimal("-15.99"),
            next_date=date.today() + timedelta(days=3),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            account_id=card.id,
            notes="Added from recurring detection · pays_from=card",
            active=True,
        )
    )
    s.commit()
    r = run_ifpp(s, profile_id=p.id)
    assert r.details.get("skipped_credit_account_schedules") == 1
    assert r.details.get("skipped_card_autopay_schedules") == 1
    # Cash Safe stays ~full balance (no Netflix cash double-count)
    assert r.cash_spendable >= Decimal("1990")
    s.close()


def test_detect_defaults_to_card_account(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
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
        nickname="Amex",
        current_balance=Decimal("200"),
        credit_limit=Decimal("10000"),
    )
    s.add_all([cash, card])
    s.flush()
    as_of = date.today()
    for i in range(3):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=card.id,
                txn_date=as_of - timedelta(days=30 * (i + 1)),
                amount=Decimal("-15.99"),
                payee="Netflix.com",
                status="cleared",
                is_transfer=False,
            )
        )
    s.commit()
    det = detect_recurring(s, profile_id=p.id, as_of=as_of, min_occurrences=2, limit=10)
    net = [x for x in det.get("suggestions") or [] if "netflix" in (x.get("name") or "").lower()]
    assert net, det
    assert net[0].get("pays_from") == "card"
    assert int(net[0].get("suggested_account_id") or 0) == card.id

    res = accept_recurring_suggestion(
        s,
        name=net[0]["name"],
        amount=net[0]["amount_abs"],
        cadence=net[0].get("cadence") or "monthly",
        next_date=net[0].get("suggested_next_date"),
        profile_id=p.id,
        account_id=int(net[0]["suggested_account_id"]),
    )
    s.commit()
    row = s.get(ScheduledItem, res["scheduled_id"])
    assert row is not None
    assert row.account_id == card.id
    assert "pays_from=card" in (row.notes or "")
    s.close()
