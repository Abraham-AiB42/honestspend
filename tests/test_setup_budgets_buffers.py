"""Setup budgets seed review + dual buffers + IFPP effective buffer."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Category, Profile, Transaction, init_db
from honestspend.engine.ifpp import CashAccountView, compute_cash_spendable
from honestspend.seed import seed_all
from honestspend.services.ifpp_service import run_ifpp
from honestspend.services.pre_purchase import check_purchase
from honestspend.services.setup_budgets import budgets_review, buffers_status, save_buffers


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


def test_effective_buffer_max_of_total_and_per_account():
    accounts = [
        CashAccountView(1, "A", Decimal("2000"), True, "checking", Decimal("500")),
        CashAccountView(2, "B", Decimal("1000"), True, "checking", Decimal("200")),
    ]
    # total floor 300, per-sum 700 → effective 700
    cash, _, _, _ = compute_cash_spendable(
        accounts,
        [],
        as_of=date(2026, 8, 10),
        mode="conservative",
        safety_buffer=Decimal("300"),
        horizon_days=14,
    )
    # 3000 - 700 = 2300
    assert cash == Decimal("2300")


def test_save_buffers_and_ifpp(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    a = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("800"),
    )
    s.add(a)
    s.commit()
    save_buffers(s, total_buffer=Decimal("1000"), account_buffers=[{"id": a.id, "safety_buffer": 800}])
    s.commit()
    st = buffers_status(s, profile_id=p.id)
    assert Decimal(st["effective_buffer"]) == Decimal("1000")  # max(1000, 800)
    ifpp = run_ifpp(s, profile_id=p.id)
    # 5000 - 1000 buffer = 4000 (no bills)
    assert ifpp.cash_spendable == Decimal("4000.00")
    s.close()


def test_pre_purchase_lists_cash_accounts(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    settings_row = s.get(__import__("honestspend.db", fromlist=["AppSettings"]).AppSettings, 1)
    settings_row.safety_buffer = Decimal("0")
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Primary",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
            safety_buffer=Decimal("500"),
        )
    )
    s.commit()
    res = check_purchase(s, amount=Decimal("100"), prefer="cash", profile_id=p.id)
    cash_opts = [o for o in res["options"] if o["method"] == "cash" and o.get("account_id")]
    assert cash_opts
    assert cash_opts[0]["safe"] is True
    s.close()


def test_budgets_review_seeds(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("4000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    cat = s.query(Category).filter(Category.profile_id == p.id).first()
    assert cat
    for i in range(15):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=cat.id,
                txn_date=date.today() - timedelta(days=i * 2),
                amount=Decimal("-20"),
                payee="Spend",
            )
        )
    s.commit()
    rev = budgets_review(s, profile_id=p.id, seed_if_empty=True)
    assert rev["count"] >= 1 or rev["seed"] is not None
    s.close()
