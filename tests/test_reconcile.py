"""Reconcile books vs institution balance."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.reconcile import reconcile_report, set_institution_balance, trust_balance


def test_reconcile_drift_and_trust(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    a = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Main",
        current_balance=Decimal("1000"),
        is_cash_for_ifpp=True,
    )
    s.add(a)
    s.commit()

    set_institution_balance(s, a.id, Decimal("950"))
    s.commit()
    rep = reconcile_report(s)
    assert rep["drifted"] >= 1
    row = next(x for x in rep["accounts"] if x["account_id"] == a.id)
    assert row["status"] == "drift"
    assert Decimal(row["drift"]) == Decimal("50.00")

    trust_balance(s, a.id, trust="institution")
    s.commit()
    s.refresh(a)
    assert a.current_balance == Decimal("950")
    rep2 = reconcile_report(s)
    row2 = next(x for x in rep2["accounts"] if x["account_id"] == a.id)
    assert row2["status"] == "matched"
    s.close()
