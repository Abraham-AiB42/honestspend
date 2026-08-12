"""Fingerprint upsert + D10 user-vs-incoming promo conflicts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, PromoConflict, PromoInstallmentLine, init_db
from honestspend.seed import seed_all
from honestspend.services.promo_installments import create_promo_line, update_promo_line
from honestspend.services.promo_upsert import (
    list_open_conflicts,
    promo_fingerprint,
    resolve_promo_conflict,
    upsert_promo_term,
)


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


def _credit(s, *, nickname: str = "Card", balance: str = "1000") -> Account:
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname=nickname,
        current_balance=Decimal(balance),
        credit_limit=Decimal("10000"),
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    return card


def test_promo_fingerprint_purchase_plan_uses_monthly():
    fp = promo_fingerprint(
        account_id=7,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        monthly=Decimal("29.01"),
        end_date=None,
    )
    assert fp == "7|purchase_plan|amazon mixmaster|29.01"


def test_promo_fingerprint_card_intro_uses_end_and_fallback_name():
    fp = promo_fingerprint(
        account_id=3,
        kind="card_intro",
        name="",
        monthly=None,
        end_date=date(2027, 3, 15),
    )
    assert fp == "3|card_intro|__intro__|"
    named = promo_fingerprint(
        account_id=3,
        kind="card_intro",
        name="Chase Freedom Intro",
        monthly=None,
        end_date=date(2027, 3, 15),
    )
    assert named == "3|card_intro|chase freedom intro|2027-03-15"


def test_statement_creates_new_amazon_line(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()
    assert r["created"] is True
    assert r["conflict"] is None
    line = r["line"]
    assert isinstance(line, PromoInstallmentLine)
    assert line.name == "Amazon Mixmaster"
    assert line.principal_remaining == Decimal("348.12")
    assert line.monthly_payment == Decimal("29.01")
    assert line.source == "statement"
    assert line.kind == "purchase_plan"
    assert line.fingerprint == promo_fingerprint(
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        monthly=Decimal("29.01"),
        end_date=None,
    )
    assert s.query(PromoInstallmentLine).count() == 1


def test_second_scrape_updates_remaining(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    first = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.flush()
    line_id = first["line"].id
    second = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("319.11"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()
    assert second["created"] is False
    assert second["conflict"] is None
    assert second["line"].id == line_id
    assert second["line"].principal_remaining == Decimal("319.11")
    assert s.query(PromoInstallmentLine).count() == 1
    assert s.query(PromoConflict).count() == 0


def test_user_line_plus_different_statement_creates_conflict(
    tmp_path: Path, monkeypatch
):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    user = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("400.00"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="user",
    )
    s.flush()
    user_remaining = user["line"].principal_remaining
    line_id = user["line"].id

    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()

    assert r["created"] is False
    assert r["conflict"] is not None
    assert r["line"].id == line_id
    assert r["line"].principal_remaining == user_remaining
    assert r["line"].principal_remaining == Decimal("400.00")
    assert s.query(PromoConflict).filter(PromoConflict.resolved_at.is_(None)).count() == 1

    open_c = list_open_conflicts(s, account_id=card.id)
    assert len(open_c) == 1
    assert open_c[0]["line_id"] == line_id
    assert open_c[0]["incoming_source"] == "statement"


def test_take_incoming_updates_remaining(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("400.00"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="user",
    )
    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.flush()
    conflict_id = r["conflict"]["id"]

    line = resolve_promo_conflict(s, conflict_id, action="take_incoming")
    s.commit()

    assert line.principal_remaining == Decimal("348.12")
    assert line.source == "statement"
    conf = s.get(PromoConflict, conflict_id)
    assert conf is not None
    assert conf.resolved_at is not None
    assert list_open_conflicts(s, account_id=card.id) == []


def test_plaid_cannot_silent_update_source_statement(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    first = upsert_promo_term(
        s,
        account_id=card.id,
        kind="card_intro",
        name="Intro 0%",
        principal_remaining=Decimal("2100.00"),
        monthly_payment=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=date(2027, 3, 15),
        source="statement",
        apr=Decimal("0"),
    )
    s.flush()
    line_id = first["line"].id
    remaining_before = first["line"].principal_remaining

    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="card_intro",
        name="Intro 0%",
        principal_remaining=Decimal("1900.00"),
        monthly_payment=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=date(2027, 3, 15),
        source="plaid",
        apr=Decimal("0"),
    )
    s.commit()

    assert r["created"] is False
    assert r["conflict"] is not None
    assert r["line"].id == line_id
    assert r["line"].principal_remaining == remaining_before
    assert r["line"].source == "statement"
    assert s.query(PromoConflict).filter(PromoConflict.resolved_at.is_(None)).count() == 1


def test_promo_fingerprint_intro_zero_and_unnamed_share_identity():
    """Unnamed / 'Intro 0%' card intros fingerprint as __intro__ (end_date omitted)."""
    dated = promo_fingerprint(
        account_id=3,
        kind="card_intro",
        name="Intro 0%",
        monthly=None,
        end_date=date(2027, 3, 15),
    )
    undated = promo_fingerprint(
        account_id=3,
        kind="card_intro",
        name="",
        monthly=None,
        end_date=None,
    )
    assert dated == undated
    assert dated == "3|card_intro|__intro__|"


def test_create_promo_line_without_fingerprint_then_statement_conflicts(
    tmp_path: Path, monkeypatch
):
    """create_promo_line (no fingerprint) + statement same name/monthly → one line, conflict."""
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    line = create_promo_line(
        s,
        card.id,
        name="Amazon Mixmaster",
        principal_remaining=Decimal("400.00"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        source="user",
        kind="purchase_plan",
    )
    s.flush()
    expected_fp = promo_fingerprint(
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        monthly=Decimal("29.01"),
        end_date=None,
    )
    assert line.fingerprint == expected_fp

    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()

    assert r["created"] is False
    assert r["conflict"] is not None
    assert r["line"].id == line.id
    assert r["line"].principal_remaining == Decimal("400.00")
    assert s.query(PromoInstallmentLine).count() == 1


def test_unsourced_user_line_matches_by_account_kind_name(
    tmp_path: Path, monkeypatch
):
    """Already-saved user rows (fingerprint NULL) still collide on name identity."""
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    line = create_promo_line(
        s,
        card.id,
        name="Amazon Mixmaster",
        principal_remaining=Decimal("400.00"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        source="user",
        kind="purchase_plan",
        fingerprint=None,
    )
    line.fingerprint = None
    s.flush()
    assert line.fingerprint is None

    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()

    assert r["created"] is False
    assert r["conflict"] is not None
    assert r["line"].id == line.id
    assert r["line"].principal_remaining == Decimal("400.00")
    assert s.query(PromoInstallmentLine).count() == 1


def test_statement_intro_then_plaid_special_is_one_card_intro(
    tmp_path: Path, monkeypatch
):
    """Dated statement Intro 0% + Plaid special 0% (no end) share default intro identity."""
    from honestspend.services.import_bootstrap import apply_statement_promos
    from honestspend.services.plaid_service import apply_plaid_promo_aprs

    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    text = "Promotional APR 0% through 03/15/2027  Balance subject to promo $2,100.00"
    apply_statement_promos(s, card.id, text, as_of=date(2026, 8, 1))
    s.flush()
    apply_plaid_promo_aprs(
        s,
        card.id,
        [
            {
                "apr_percentage": 0,
                "apr_type": "special",
                "balance_subject_to_apr": 2100,
                "interest_charge_amount": 0,
            }
        ],
    )
    s.commit()

    lines = (
        s.query(PromoInstallmentLine)
        .filter(PromoInstallmentLine.account_id == card.id)
        .all()
    )
    intros = [ln for ln in lines if ln.kind == "card_intro"]
    assert len(intros) == 1
    assert intros[0].end_date == date(2027, 3, 15)


def test_statement_fills_empty_end_on_plaid_intro(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    plaid = upsert_promo_term(
        s,
        account_id=card.id,
        kind="card_intro",
        name="Intro 0%",
        principal_remaining=Decimal("2100.00"),
        monthly_payment=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=None,
        source="plaid",
        apr=None,
    )
    s.flush()
    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="card_intro",
        name="Intro 0%",
        principal_remaining=Decimal("2100.00"),
        monthly_payment=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=date(2027, 3, 15),
        source="statement",
        apr=Decimal("0"),
    )
    s.commit()
    assert r["created"] is False
    assert r["conflict"] is None
    assert r["line"].id == plaid["line"].id
    assert r["line"].end_date == date(2027, 3, 15)
    assert r["line"].apr == Decimal("0")
    assert r["line"].source == "statement"
    assert s.query(PromoInstallmentLine).count() == 1


def test_user_patch_remaining_sets_source_user_then_statement_conflicts(
    tmp_path: Path, monkeypatch
):
    """Editor save on a statement line becomes source=user; next scrape conflicts."""
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    first = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.flush()
    line = update_promo_line(
        s,
        first["line"].id,
        principal_remaining=Decimal("400.00"),
        end_date=date(2027, 1, 15),
    )
    s.flush()
    assert line.source == "user"
    assert line.end_date == date(2027, 1, 15)

    r = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()
    assert r["created"] is False
    assert r["conflict"] is not None
    assert r["line"].principal_remaining == Decimal("400.00")
    assert r["line"].source == "user"
    assert s.query(PromoInstallmentLine).count() == 1


def test_insert_conflict_reuses_unresolved_same_line_and_source(
    tmp_path: Path, monkeypatch
):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("400.00"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="user",
    )
    first = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("348.12"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.flush()
    first_id = first["conflict"]["id"]
    second = upsert_promo_term(
        s,
        account_id=card.id,
        kind="purchase_plan",
        name="Amazon Mixmaster",
        principal_remaining=Decimal("319.11"),
        monthly_payment=Decimal("29.01"),
        start_date=date(2026, 8, 1),
        end_date=None,
        source="statement",
    )
    s.commit()
    assert second["conflict"] is not None
    assert second["conflict"]["id"] == first_id
    assert s.query(PromoConflict).filter(PromoConflict.resolved_at.is_(None)).count() == 1
    assert second["conflict"]["incoming_values"]["principal_remaining"] == "319.11"


def test_list_open_conflicts_account_scoped(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card_a = _credit(s, nickname="A")
    card_b = _credit(s, nickname="B")
    for card in (card_a, card_b):
        upsert_promo_term(
            s,
            account_id=card.id,
            kind="purchase_plan",
            name="Amazon Mixmaster",
            principal_remaining=Decimal("400.00"),
            monthly_payment=Decimal("29.01"),
            start_date=date(2026, 8, 1),
            end_date=None,
            source="user",
        )
        upsert_promo_term(
            s,
            account_id=card.id,
            kind="purchase_plan",
            name="Amazon Mixmaster",
            principal_remaining=Decimal("348.12"),
            monthly_payment=Decimal("29.01"),
            start_date=date(2026, 8, 1),
            end_date=None,
            source="statement",
        )
    s.commit()
    only_a = list_open_conflicts(s, account_id=card_a.id)
    only_b = list_open_conflicts(s, account_id=card_b.id)
    all_open = list_open_conflicts(s)
    assert len(only_a) == 1
    assert len(only_b) == 1
    assert only_a[0]["line_id"] != only_b[0]["line_id"]
    assert len(all_open) == 2
