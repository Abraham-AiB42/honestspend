"""Promo sinking fund bill creation."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.promo_sink import create_promo_sink_bill


def test_create_promo_sink(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=personal.id,
        kind="credit",
        nickname="0% Card",
        current_balance=Decimal("3000"),
        credit_limit=Decimal("10000"),
        available_credit=Decimal("7000"),
        promo_apr=Decimal("0"),
        promo_end_date=date.today() + timedelta(days=90),
        promo_balance=Decimal("3000"),
        payment_due_day=15,
        is_cash_for_ifpp=False,
    )
    s.add(card)
    s.commit()

    out = create_promo_sink_bill(s, account_id=card.id)
    s.commit()
    assert out["ok"]
    assert out["created"] is True
    assert Decimal(out["monthly_sink"]) > 0

    row = s.get(ScheduledItem, out["scheduled_id"])
    assert row is not None
    assert row.active is True
    assert row.amount < 0

    out2 = create_promo_sink_bill(s, account_id=card.id)
    s.commit()
    assert out2["updated"] is True
    assert out2["scheduled_id"] == out["scheduled_id"]
    s.close()
