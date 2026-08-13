"""Entity year P&L — income / expense / owner_draw / payroll / tax buckets."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Category, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.entity_pnl import build_entity_pnl
from honestspend.services.profiles import create_profile


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


def _biz_setup(s):
    biz = create_profile(s, display_name="Agency Co", entity_type="business")
    s.flush()
    cash = Account(
        profile_id=biz.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("10000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    return biz, cash


def _cat(s, *, code: str | None = None, display_name: str | None = None, profile_id: int | None = None):
    q = s.query(Category)
    if code:
        return q.filter(Category.code == code).one()
    if display_name and profile_id is not None:
        return (
            q.filter(Category.profile_id == profile_id, Category.display_name == display_name)
            .one()
        )
    if display_name:
        return q.filter(Category.display_name == display_name).first()
    raise ValueError("code or display_name required")


def test_build_entity_pnl_buckets(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    biz, cash = _biz_setup(s)
    year = 2026

    income_cat = (
        s.query(Category)
        .filter(Category.profile_id == biz.id, Category.budget_group == "income")
        .first()
    )
    assert income_cat is not None

    payroll_cat = (
        s.query(Category)
        .filter(Category.profile_id == biz.id, Category.budget_group == "payroll")
        .first()
    )
    assert payroll_cat is not None

    owner_cat = _cat(s, code="SYS_OWNER_DISTRIBUTION")
    tax_cat = (
        s.query(Category)
        .filter(Category.profile_id == biz.id, Category.code.like("%TAXES%"))
        .first()
    )
    meals = (
        s.query(Category)
        .filter(Category.profile_id == biz.id, Category.partial_rule == "meals_50")
        .first()
    )

    s.add_all(
        [
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=income_cat.id,
                txn_date=date(year, 2, 1),
                amount=Decimal("5000.00"),
                payee="Client A",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=payroll_cat.id if payroll_cat else None,
                txn_date=date(year, 2, 15),
                amount=Decimal("-2000.00"),
                payee="Biweekly payroll · net",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=tax_cat.id if tax_cat else None,
                txn_date=date(year, 2, 15),
                amount=Decimal("-300.00"),
                payee="Biweekly payroll · employer tax",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=owner_cat.id,
                txn_date=date(year, 3, 1),
                amount=Decimal("-500.00"),
                payee="Owner draw",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=meals.id if meals else None,
                txn_date=date(year, 3, 10),
                amount=Decimal("-100.00"),
                payee="Client lunch",
            ),
            # Outside year — ignored
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=income_cat.id,
                txn_date=date(year - 1, 12, 31),
                amount=Decimal("999.00"),
                payee="Old year",
            ),
            # Transfer — skipped
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=_cat(s, code="SYS_TRANSFER").id,
                txn_date=date(year, 4, 1),
                amount=Decimal("-50.00"),
                payee="Move money",
                is_transfer=True,
            ),
        ]
    )
    s.commit()

    pnl = build_entity_pnl(s, biz.id, year)
    assert pnl["profile_id"] == biz.id
    assert pnl["year"] == year
    assert Decimal(pnl["income"]) == Decimal("5000.00")
    assert Decimal(pnl["payroll"]) == Decimal("-2000.00")
    assert Decimal(pnl["taxes"]) == Decimal("-300.00")
    assert Decimal(pnl["owner_draws"]) == Decimal("-500.00")
    assert Decimal(pnl["expenses"]) == Decimal("-100.00")
    # net = signed sum of buckets
    assert Decimal(pnl["net"]) == Decimal("2100.00")
    assert isinstance(pnl["by_category"], list)
    assert len(pnl["by_category"]) >= 1
    cats = {r["category"] for r in pnl["by_category"]}
    assert any("meals" in c.lower() or c == "Uncategorized" or "Gross" in c or "Owner" in c for c in cats)
    s.close()


def test_name_hints_payroll_and_employer_tax(tmp_path: Path, monkeypatch):
    """Uncategorized payees still bucket via payroll / employer tax name hints."""
    s = _session(tmp_path, monkeypatch)
    biz, cash = _biz_setup(s)
    year = 2026
    s.add_all(
        [
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=None,
                txn_date=date(year, 5, 1),
                amount=Decimal("-1500.00"),
                payee="Package net run",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=None,
                txn_date=date(year, 5, 1),
                amount=Decimal("-250.00"),
                payee="Employer tax remittance",
            ),
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=None,
                txn_date=date(year, 5, 2),
                amount=Decimal("3000.00"),
                payee="Deposit",
            ),
        ]
    )
    s.commit()
    pnl = build_entity_pnl(s, biz.id, year)
    assert Decimal(pnl["payroll"]) == Decimal("-1500.00")
    assert Decimal(pnl["taxes"]) == Decimal("-250.00")
    assert Decimal(pnl["income"]) == Decimal("3000.00")
    s.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "require_api_key", False)
    import honestspend.api.app as app_mod
    from honestspend.db import make_engine, make_session_factory

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as sess:
        seed_all(sess)
        sess.commit()
    with TestClient(app_mod.app) as c:
        yield c


def test_entity_pnl_api(client: TestClient):
    import honestspend.api.app as app_mod

    with app_mod.SessionLocal() as s:
        biz = create_profile(s, display_name="API Biz", entity_type="business")
        s.flush()
        cash = Account(
            profile_id=biz.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        s.add(cash)
        s.flush()
        income = (
            s.query(Category)
            .filter(Category.profile_id == biz.id, Category.budget_group == "income")
            .first()
        )
        s.add(
            Transaction(
                profile_id=biz.id,
                account_id=cash.id,
                category_id=income.id if income else None,
                txn_date=date(2026, 6, 1),
                amount=Decimal("100.00"),
                payee="Sale",
            )
        )
        s.commit()
        pid = biz.id

    r = client.get(f"/api/reports/entity-pnl?profile_id={pid}&year=2026")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_id"] == pid
    assert body["year"] == 2026
    assert "income" in body and "net" in body and "by_category" in body
    assert Decimal(body["income"]) == Decimal("100.00")

    missing = client.get("/api/reports/entity-pnl?profile_id=99999&year=2026")
    assert missing.status_code == 404
