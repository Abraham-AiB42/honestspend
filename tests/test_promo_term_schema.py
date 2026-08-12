"""Schema 23: promo term fields on installment lines + promo_conflicts."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from honestspend.db import init_db
from honestspend.migrations import SCHEMA_VERSION, get_schema_version


def test_schema_23_promo_term_columns(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    init_db(eng)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 23
    cols = {c["name"] for c in inspect(eng).get_columns("promo_installment_lines")}
    for name in ("kind", "offer_type", "fingerprint", "linked_txn_id", "status", "apr"):
        assert name in cols
    assert "promo_conflicts" in inspect(eng).get_table_names()
    # account_id must allow NULL (new_card offer)
    acct_col = next(
        c for c in inspect(eng).get_columns("promo_installment_lines") if c["name"] == "account_id"
    )
    assert acct_col["nullable"] is True
