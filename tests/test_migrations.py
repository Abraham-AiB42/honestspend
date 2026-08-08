"""Schema migration runner."""

from pathlib import Path

from sqlalchemy import create_engine, text

from financial_os.db import init_db
from financial_os.migrations import SCHEMA_VERSION, get_schema_version


def test_migrations_reach_target(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    init_db(eng)
    assert get_schema_version(eng) == SCHEMA_VERSION
    with eng.connect() as conn:
        # schema_meta exists
        row = conn.execute(text("SELECT version FROM schema_meta WHERE id = 1")).fetchone()
        assert row is not None
        assert int(row[0]) == SCHEMA_VERSION
