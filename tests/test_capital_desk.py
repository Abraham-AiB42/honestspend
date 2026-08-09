from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, AppSettings, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.capital_desk import build_capital_desk
from financial_os.engine.ifpp import CashAccountView, compute_cash_spendable


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_checking_scope_ignores_savings_for_spendable():
    as_of = date(2026, 8, 6)
    cash = [
        CashAccountView(1, "Checking", Decimal("500"), True, "checking"),
        CashAccountView(2, "HYSA", Decimal("10000"), True, "savings"),
    ]
    spend, _, _, red_now = compute_cash_spendable(
        cash,
        [],
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        never_negative_scope="checking",
    )
    assert spend == Decimal("500.00")
    assert red_now is False


def test_negative_checking_warns():
    as_of = date(2026, 8, 6)
    cash = [CashAccountView(1, "Checking", Decimal("-50"), True, "checking")]
    _, red_day, warnings, red_now = compute_cash_spendable(
        cash,
        [],
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("0"),
        never_negative_scope="checking",
    )
    assert any("CHECKING NEGATIVE" in w for w in warnings)
    assert red_now is True
    assert red_day == as_of


def test_capital_desk_headline_protects_when_broke(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("1000")
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("100"),
            is_cash_for_ifpp=True,
        )
    )
    s.flush()
    desk = build_capital_desk(s)
    assert desk["headline"]["action"] in ("protect_checking", "top_up_buffer", "stop_fees", "park_yield", "hold")
    assert desk["headline"]["title"]
    assert "principles" in desk
    s.close()


def test_capital_desk_flags_fees(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("0")
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            txn_date=date.today() - timedelta(days=2),
            amount=Decimal("-35"),
            payee="OVERDRAFT FEE",
        )
    )
    s.flush()
    desk = build_capital_desk(s)
    actions = [st["action"] for st in desk["steps"]]
    assert "stop_fees" in actions
    s.close()
