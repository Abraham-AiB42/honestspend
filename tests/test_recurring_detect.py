"""Recurring bill detector (dream H1-A2)."""

from datetime import date, timedelta
from decimal import Decimal

from financial_os.db import Account, Profile, Transaction, init_db, make_engine, make_session_factory
from financial_os.seed import seed_all
from financial_os.services.recurring_detect import detect_recurring, normalize_payee


def test_normalize_payee():
    assert "netflix" in normalize_payee("NETFLIX.COM 12345")
    assert normalize_payee("Spotify USA") == "spotify usa"


def test_detect_monthly_subscription(tmp_path):
    eng = make_engine()
    # override data dir not needed for in-memory-ish file
    from financial_os.config import settings

    settings.data_dir = tmp_path / "d"
    settings.data_dir.mkdir()
    eng = make_engine()
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(Profile).filter(Profile.slug == "personal").one()
        acct = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("2000"),
            is_cash_for_ifpp=True,
        )
        s.add(acct)
        s.flush()
        base = date.today().replace(day=1)
        for i in range(4):
            s.add(
                Transaction(
                    profile_id=personal.id,
                    account_id=acct.id,
                    txn_date=base - timedelta(days=30 * i),
                    amount=Decimal("-15.99"),
                    payee="NETFLIX.COM",
                    status="cleared",
                    is_transfer=False,
                )
            )
        s.flush()
        out = detect_recurring(s, profile_id=personal.id, min_occurrences=2)
        assert out["count"] >= 1
        names = " ".join(x["name"].upper() for x in out["suggestions"])
        assert "NETFLIX" in names
        assert out["needs_attention"] is True
        sug = out["suggestions"][0]
        from financial_os.services.recurring_detect import accept_recurring_suggestion

        acc = accept_recurring_suggestion(
            s,
            name=sug["name"],
            amount=sug["amount_abs"],
            cadence=sug["cadence"],
            next_date=sug.get("suggested_next_date"),
            profile_id=personal.id,
        )
        assert acc["ok"] is True
        assert acc["created"] is True
