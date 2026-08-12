"""Safe until soft line caps at Safe to spend (one Home cash spine)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Account, AppSettings, Profile, ScheduledItem, init_db
from honestspend.seed import seed_all
from honestspend.services.cash_runway import build_safe_until_window
from honestspend.services.home_simple import build_home_simple


def _session(tmp_path: Path, monkeypatch):
    from honestspend.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    st = s.get(AppSettings, 1)
    st.safety_buffer = Decimal("0")
    st.tax_vault_enabled = False
    s.commit()
    return s


def test_safe_until_capped_at_sts(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash)
    s.flush()
    # Small coming-up outflow so soft raw would be high
    s.add(
        ScheduledItem(
            profile_id=p.id,
            account_id=cash.id,
            name="Small bill",
            amount=Decimal("-50"),
            next_date=date(2026, 3, 5),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    s.commit()

    # Cap at $500 (as if STS after big budget reserve)
    soft = build_safe_until_window(
        s, as_of=as_of, profile_id=p.id, cap_at=Decimal("500")
    )
    assert Decimal(soft["amount"]) == Decimal("500.00")
    assert soft["capped_to_safe_to_spend"] is True
    assert "within Safe to spend" in soft["label"]
    assert Decimal(soft["raw_amount"]) > Decimal("500")
    s.close()


def test_home_simple_soft_never_exceeds_sts(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("1500"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash)
    s.commit()
    home = build_home_simple(s, as_of=as_of, profile_id=p.id)
    sts = Decimal(home["safe_to_spend"])
    soft = Decimal(home["safe_until_window"]["amount"])
    assert soft <= sts + Decimal("0.01")
    # User-facing soft labels must not use engine "horizon"
    assert "horizon" not in (home["safe_until_window"].get("label") or "").lower()
    s.close()


def test_soft_until_starting_cash_override(tmp_path: Path, monkeypatch):
    """When starting_cash is set, soft base is that value (not runway start).

    post_reserve=1500, outflow=200 → amount=1300 even if books cash is 2000.
    """
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash)
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            account_id=cash.id,
            name="Window bill",
            amount=Decimal("-200"),
            next_date=date(2026, 3, 5),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    s.commit()

    from honestspend.services.coming_up import build_coming_up

    cu = build_coming_up(s, as_of=as_of, profile_id=p.id, mode="calendar", calendar_days=14)
    assert Decimal(cu["outflow_total"]) == Decimal("200.00")

    soft = build_safe_until_window(
        s,
        as_of=as_of,
        profile_id=p.id,
        coming_up=cu,
        starting_cash=Decimal("1500"),
        cap_at=Decimal("1500"),
    )
    assert Decimal(soft["starting_cash"]) == Decimal("1500.00")
    assert Decimal(soft["outflows"]) == Decimal("200.00")
    assert Decimal(soft["amount"]) == Decimal("1300.00")
    assert soft["capped_to_safe_to_spend"] is False
    s.close()


def test_home_soft_single_counts_window_outflows(tmp_path: Path, monkeypatch):
    """Home soft starts from runway − budgets (pre-schedule), not STS.

    STS (ifpp.cash_spendable − reserve) already reserved window bills. Using
    STS as start then subtracting Coming up outflows double-counts. Correct:
    soft_start = max(0, runway_start − reserve); soft = max(0, soft_start − outflows);
    cap at STS.
    """
    from honestspend.db import Category
    from honestspend.services.budget_service import create_rule
    from honestspend.services.cash_runway import runway_starting_cash

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash_acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash_acct)
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            account_id=cash_acct.id,
            name="Rent",
            amount=Decimal("-400"),
            next_date=date(2026, 3, 5),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    # Budget envelope → reserve > 0 so soft_start / STS both net budgets
    cat = (
        s.query(Category)
        .filter(Category.profile_id == p.id)
        .order_by(Category.id)
        .first()
    )
    assert cat is not None
    create_rule(
        s,
        profile_id=p.id,
        category_id=cat.id,
        period="monthly",
        amount=Decimal("500"),
        name="Groceries",
    )
    s.commit()

    home = build_home_simple(s, as_of=as_of, profile_id=p.id)
    sts = Decimal(home["safe_to_spend"])
    reserve = Decimal(home["budget_reserve"])
    soft_amt = Decimal(home["safe_until_window"]["amount"])
    soft_start = Decimal(home["safe_until_window"]["starting_cash"])
    outflows = Decimal(home["safe_until_window"]["outflows"])

    runway_start, _, _, _ = runway_starting_cash(s, profile_id=p.id)
    expected_start = max(Decimal("0"), runway_start - reserve)
    expected_raw = max(Decimal("0"), expected_start - outflows)
    expected_soft = min(sts, expected_raw)

    assert reserve > Decimal("0"), "need budget reserve for this scenario"
    assert outflows > Decimal("0")
    assert soft_start == expected_start.quantize(Decimal("0.01"))
    # Pre-schedule base, not post-IFPP STS (would double-count if equal with outflows)
    assert soft_start == (runway_start - reserve).quantize(Decimal("0.01"))
    assert soft_amt == expected_soft.quantize(Decimal("0.01"))
    assert soft_amt <= sts
    # Must not understate via STS − outflows (double-subtract)
    double_count = max(Decimal("0"), sts - outflows)
    assert soft_amt >= double_count
    s.close()


def test_home_soft_not_sts_minus_bills_double_count(tmp_path: Path, monkeypatch):
    """With window bills already in IFPP, soft must not be STS − outflows."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 3, 1)
    cash_acct = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(cash_acct)
    s.flush()
    s.add(
        ScheduledItem(
            profile_id=p.id,
            account_id=cash_acct.id,
            name="Utilities",
            amount=Decimal("-300"),
            next_date=date(2026, 3, 4),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    s.commit()

    home = build_home_simple(s, as_of=as_of, profile_id=p.id)
    sts = Decimal(home["safe_to_spend"])
    soft_amt = Decimal(home["safe_until_window"]["amount"])
    soft_start = Decimal(home["safe_until_window"]["starting_cash"])
    outflows = Decimal(home["safe_until_window"]["outflows"])

    assert outflows == Decimal("300.00")
    # Pre-schedule start ≈ books cash (buffer 0)
    assert soft_start == Decimal("2000.00")
    # Single-count residual after window (capped at STS)
    assert soft_amt == Decimal("1700.00") or soft_amt == sts
    assert soft_amt == min(sts, Decimal("1700.00"))
    # Old bug: soft = STS − outflows understated by another $300
    assert soft_amt != max(Decimal("0"), sts - outflows) or outflows == Decimal("0")
    assert soft_amt > max(Decimal("0"), sts - outflows)
    s.close()
