"""Schema: funding account, cycle cache columns, statement_cycles table."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, StatementCycle, init_db
from financial_os.migrations import SCHEMA_VERSION, get_schema_version
from financial_os.seed import seed_all


def _engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'schema.db').as_posix()}")
    init_db(eng)
    return eng


def test_account_has_funding_and_cycle_cache(tmp_path: Path):
    eng = _engine(tmp_path)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 17

    assert hasattr(Account, "payment_funding_account_id")
    assert hasattr(Account, "cycle_config_source")
    assert hasattr(Account, "statement_balance_cached")
    assert hasattr(Account, "next_payment_amount_cached")
    assert hasattr(Account, "next_payment_date_cached")

    insp = inspect(eng)
    cols = {c["name"] for c in insp.get_columns("accounts")}
    for name in (
        "payment_funding_account_id",
        "cycle_config_source",
        "statement_balance_cached",
        "next_payment_amount_cached",
        "next_payment_date_cached",
    ):
        assert name in cols, f"accounts missing column {name}"

    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        seed_all(s)
        s.commit()
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="Funding",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=p.id,
            kind="credit",
            nickname="Visa",
            current_balance=Decimal("250"),
            credit_limit=Decimal("5000"),
            payment_due_day=15,
            statement_close_day=1,
            cycle_config_source="default",
        )
        s.add_all([cash, card])
        s.flush()
        card.payment_funding_account_id = cash.id
        card.statement_balance_cached = Decimal("250.00")
        card.next_payment_amount_cached = Decimal("250.00")
        card.next_payment_date_cached = date(2026, 8, 15)
        s.commit()

        loaded = s.get(Account, card.id)
        assert loaded.payment_funding_account_id == cash.id
        assert loaded.cycle_config_source == "default"
        assert loaded.statement_balance_cached == Decimal("250.00")
        assert loaded.next_payment_amount_cached == Decimal("250.00")
        assert loaded.next_payment_date_cached == date(2026, 8, 15)
    finally:
        s.close()


def test_statement_cycles_table_exists_and_roundtrips(tmp_path: Path):
    eng = _engine(tmp_path)
    assert "statement_cycles" in inspect(eng).get_table_names()

    sc_cols = {c["name"] for c in inspect(eng).get_columns("statement_cycles")}
    for name in (
        "id",
        "account_id",
        "cycle_start",
        "cycle_end",
        "due_date",
        "projected_balance",
        "actual_balance",
        "payment_amount",
        "payment_funding_account_id",
        "status",
        "source",
    ):
        assert name in sc_cols, f"statement_cycles missing column {name}"

    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        seed_all(s)
        s.commit()
        p = s.query(Profile).filter(Profile.slug == "personal").one()
        card = Account(
            profile_id=p.id,
            kind="credit",
            nickname="MC",
            current_balance=Decimal("100"),
        )
        cash = Account(
            profile_id=p.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("500"),
            is_cash_for_ifpp=True,
        )
        s.add_all([card, cash])
        s.flush()

        cycle = StatementCycle(
            account_id=card.id,
            cycle_start=date(2026, 7, 2),
            cycle_end=date(2026, 8, 1),
            due_date=date(2026, 8, 15),
            projected_balance=Decimal("100.00"),
            actual_balance=None,
            payment_amount=Decimal("100.00"),
            payment_funding_account_id=cash.id,
            status="open",
            source="projected",
        )
        s.add(cycle)
        s.commit()

        loaded = s.query(StatementCycle).filter_by(account_id=card.id).one()
        assert loaded.cycle_end == date(2026, 8, 1)
        assert loaded.due_date == date(2026, 8, 15)
        assert loaded.projected_balance == Decimal("100.00")
        assert loaded.actual_balance is None
        assert loaded.payment_amount == Decimal("100.00")
        assert loaded.payment_funding_account_id == cash.id
        assert loaded.status == "open"
        assert loaded.source == "projected"

        # SQLite pragma / FK presence sanity
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM statement_cycles WHERE account_id = :a"),
                {"a": card.id},
            ).fetchone()
            assert int(row[0]) == 1
    finally:
        s.close()
