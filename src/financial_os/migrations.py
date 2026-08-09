"""Versioned SQLite migrations for Floatpile."""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("lederring.migrations")

# Target schema version after all migrations below.
SCHEMA_VERSION = 11

# Legacy best-effort ALTERs for installs that predate schema_meta.
_LEGACY_COLUMN_SQL = [
    "ALTER TABLE scheduled_items ADD COLUMN end_date DATE",
    "ALTER TABLE scheduled_items ADD COLUMN kind VARCHAR(16) DEFAULT 'expense'",
    "ALTER TABLE scheduled_items ADD COLUMN notes TEXT",
    "ALTER TABLE scheduled_items ADD COLUMN ended_at DATETIME",
    "ALTER TABLE scheduled_items ADD COLUMN ended_reason VARCHAR(256)",
    "ALTER TABLE app_settings ADD COLUMN auto_categorize_on_import BOOLEAN DEFAULT 1",
    "ALTER TABLE accounts ADD COLUMN plaid_item_pk INTEGER",
    "ALTER TABLE accounts ADD COLUMN plaid_account_id VARCHAR(128)",
    "ALTER TABLE app_settings ADD COLUMN onboarding_complete BOOLEAN DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN product_name VARCHAR(64) DEFAULT 'Floatpile'",
    "ALTER TABLE accounts ADD COLUMN opened_date DATE",
    "ALTER TABLE accounts ADD COLUMN priority_rank INTEGER DEFAULT 100",
    "ALTER TABLE accounts ADD COLUMN apy NUMERIC(8,5)",
    "ALTER TABLE app_settings ADD COLUMN debt_strategy VARCHAR(32) DEFAULT 'avalanche'",
    "ALTER TABLE app_settings ADD COLUMN debt_extra_monthly NUMERIC(14,2) DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN opportunity_rate NUMERIC(8,5)",
    "ALTER TABLE app_settings ADD COLUMN opportunity_cost_aware BOOLEAN DEFAULT 1",
    "ALTER TABLE app_settings ADD COLUMN opportunity_tax_rate NUMERIC(5,4)",
    "ALTER TABLE app_settings ADD COLUMN credit_on_time_rate NUMERIC(5,4) DEFAULT 1",
    "ALTER TABLE app_settings ADD COLUMN credit_late_30 INTEGER DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN credit_late_60 INTEGER DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN credit_late_90 INTEGER DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN credit_hard_inquiries INTEGER DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN credit_new_accounts INTEGER DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN credit_reported_vantage INTEGER",
    "ALTER TABLE app_settings ADD COLUMN tax_vault_enabled BOOLEAN DEFAULT 1",
    "ALTER TABLE app_settings ADD COLUMN tax_vault_balance NUMERIC(14,2) DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN tax_vault_income_rate NUMERIC(5,4)",
    "ALTER TABLE app_settings ADD COLUMN income_cliff_enabled BOOLEAN DEFAULT 0",
    "ALTER TABLE app_settings ADD COLUMN income_cliff_factor NUMERIC(5,4) DEFAULT 1",
    "ALTER TABLE app_settings ADD COLUMN auto_backup_enabled BOOLEAN DEFAULT 1",
    "ALTER TABLE app_settings ADD COLUMN auto_backup_interval_hours INTEGER DEFAULT 24",
    "ALTER TABLE app_settings ADD COLUMN auto_backup_keep INTEGER DEFAULT 14",
    "ALTER TABLE app_settings ADD COLUMN auto_backup_last_at DATETIME",
    "ALTER TABLE app_users ADD COLUMN api_token VARCHAR(64)",
]


def _exec_ignore(conn, sql: str) -> None:
    try:
        conn.execute(text(sql))
    except Exception:
        pass


def _ensure_schema_meta(conn) -> int:
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_meta ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "version INTEGER NOT NULL DEFAULT 0)"
        )
    )
    row = conn.execute(text("SELECT version FROM schema_meta WHERE id = 1")).fetchone()
    if row is None:
        conn.execute(text("INSERT INTO schema_meta (id, version) VALUES (1, 0)"))
        return 0
    return int(row[0])


def _set_version(conn, version: int) -> None:
    conn.execute(text("UPDATE schema_meta SET version = :v WHERE id = 1"), {"v": version})


def _mig_1_legacy_columns(conn) -> None:
    """Bring pre-versioned DBs up with historical columns."""
    for sql in _LEGACY_COLUMN_SQL:
        _exec_ignore(conn, sql)


def _mig_2_ifpp_scope_and_archive(conn) -> None:
    _exec_ignore(
        conn,
        "ALTER TABLE app_settings ADD COLUMN ifpp_scope VARCHAR(16) DEFAULT 'entity'",
    )
    _exec_ignore(conn, "ALTER TABLE accounts ADD COLUMN archived_at DATETIME")


def _mig_3_reconcile_and_cleared(conn) -> None:
    _exec_ignore(conn, "ALTER TABLE accounts ADD COLUMN institution_balance NUMERIC(14,2)")
    _exec_ignore(conn, "ALTER TABLE accounts ADD COLUMN last_reconciled_at DATETIME")
    _exec_ignore(
        conn,
        "ALTER TABLE app_settings ADD COLUMN ifpp_cleared_only BOOLEAN DEFAULT 1",
    )


def _mig_4_profile_tax_geo(conn) -> None:
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN home_state VARCHAR(8)")
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN multi_state BOOLEAN DEFAULT 0")
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN filing_notes TEXT")
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN state_allocation_json TEXT")


def _mig_5_generic_entities(conn) -> None:
    """Public multi-entity: parent link, archive, never-neg enforcement setting."""
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN parent_profile_id INTEGER")
    _exec_ignore(conn, "ALTER TABLE profiles ADD COLUMN archived_at DATETIME")
    _exec_ignore(
        conn,
        "ALTER TABLE app_settings ADD COLUMN never_negative_enforcement "
        "VARCHAR(16) DEFAULT 'warn'",
    )


def _mig_6_fee_status(conn) -> None:
    _exec_ignore(conn, "ALTER TABLE transactions ADD COLUMN fee_status VARCHAR(32)")


def _mig_7_import_presets(conn) -> None:
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS import_presets ("
            "id INTEGER PRIMARY KEY, "
            "institution_key VARCHAR(128) UNIQUE NOT NULL, "
            "amount_sign VARCHAR(16) DEFAULT 'bank', "
            "mapping_json TEXT, "
            "notes TEXT, "
            "account_id INTEGER)"
        )
    )


def _mig_8_audit_events(conn) -> None:
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS audit_events ("
            "id INTEGER PRIMARY KEY, "
            "username VARCHAR(64) DEFAULT 'owner', "
            "role VARCHAR(32) DEFAULT 'owner', "
            "action VARCHAR(64) NOT NULL, "
            "path VARCHAR(256), "
            "detail VARCHAR(512), "
            "created_at DATETIME)"
        )
    )


def _mig_9_autopay_policy(conn) -> None:
    _exec_ignore(conn, "ALTER TABLE accounts ADD COLUMN autopay_policy VARCHAR(32)")


def _mig_10_whatif_scenarios(conn) -> None:
    _exec_ignore(
        conn,
        "CREATE TABLE IF NOT EXISTS whatif_scenarios ("
        "id INTEGER PRIMARY KEY, "
        "name VARCHAR(128) NOT NULL, "
        "profile_id INTEGER, "
        "scope VARCHAR(16) DEFAULT 'entity', "
        "extra_outflows_json TEXT DEFAULT '[]', "
        "notes VARCHAR(512), "
        "created_at DATETIME)",
    )


def _mig_11_import_reminders(conn) -> None:
    """Customizable CSV/statement download reminders (freeware money-in path)."""
    _exec_ignore(
        conn,
        "ALTER TABLE app_settings ADD COLUMN import_reminder_cadence VARCHAR(16) DEFAULT 'weekly'",
    )
    _exec_ignore(
        conn,
        "ALTER TABLE app_settings ADD COLUMN import_reminder_focus VARCHAR(16) DEFAULT 'transactions'",
    )
    _exec_ignore(conn, "ALTER TABLE app_settings ADD COLUMN import_last_at DATETIME")
    _exec_ignore(conn, "ALTER TABLE app_settings ADD COLUMN import_reminder_snooze_until DATE")


# version -> migration callable (applies that version step)
MIGRATIONS: dict[int, Callable] = {
    1: _mig_1_legacy_columns,
    2: _mig_2_ifpp_scope_and_archive,
    3: _mig_3_reconcile_and_cleared,
    4: _mig_4_profile_tax_geo,
    5: _mig_5_generic_entities,
    6: _mig_6_fee_status,
    7: _mig_7_import_presets,
    8: _mig_8_audit_events,
    9: _mig_9_autopay_policy,
    10: _mig_10_whatif_scenarios,
    11: _mig_11_import_reminders,
}


def run_migrations(engine: Engine) -> int:
    """Apply pending migrations. Returns final schema version."""
    url = str(engine.url)
    if not url.startswith("sqlite"):
        log.info("Non-SQLite URL — create_all only; migrations skipped")
        return SCHEMA_VERSION

    with engine.begin() as conn:
        current = _ensure_schema_meta(conn)
        if current > SCHEMA_VERSION:
            log.warning(
                "DB schema version %s is newer than code %s",
                current,
                SCHEMA_VERSION,
            )
            return current
        for ver in range(current + 1, SCHEMA_VERSION + 1):
            fn = MIGRATIONS.get(ver)
            if fn is None:
                raise RuntimeError(f"Missing migration for schema version {ver}")
            log.info("Applying migration v%s", ver)
            fn(conn)
            _set_version(conn, ver)
        return SCHEMA_VERSION


def get_schema_version(engine: Engine) -> int:
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT version FROM schema_meta WHERE id = 1")).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
