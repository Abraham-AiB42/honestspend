"""Live card payment schedule: recompute from statement projection after charges."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Profile, ScheduledItem, init_db
from financial_os.seed import seed_all
from financial_os.services.account_balance import apply_amount_to_account
from financial_os.services.autopay import (
    after_account_balance_changed,
    list_autopay,
    recompute_all_card_payments,
    recompute_card_payment_schedule,
    set_autopay,
    _suggested_amount,
)
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.promo_installments import create_promo_line, open_promo_totals
from financial_os.services.statement_cycle import project_card_payment


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


def _card_and_cash(s, *, bal: Decimal = Decimal("100"), policy: str = "statement"):
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("2000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Visa",
        current_balance=bal,
        credit_limit=Decimal("5000"),
        available_credit=Decimal("5000") - bal,
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy=policy,
        payment_funding_account_id=cash.id,
    )
    s.add(card)
    s.flush()
    return p, cash, card


def _card_payment_schedule(s, card_id: int) -> ScheduledItem | None:
    marker = f"card_account_id={card_id};"
    return (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.notes.like(f"%{marker}%"),
        )
        .first()
    )


def test_charge_updates_next_payment_amount(tmp_path: Path, monkeypatch):
    """card policy=statement, funding=checking, balance 100 → schedule -100;
    post charge +50 owed → schedule -150, next_date still due day.
    """
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("100.00"), policy="statement")
    # as_of = today so apply_amount recompute hook (uses date.today) matches
    as_of = date.today()

    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out["ok"] is True
    assert Decimal(str(out["next_payment"])) == Decimal("100.00")
    assert card.next_payment_amount_cached == Decimal("100.00")
    assert card.statement_balance_cached == Decimal("100.00")
    assert card.next_payment_date_cached == out["next_due"]

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.name == "Card payment · Visa"
    assert sched.amount == Decimal("-100.00")
    assert sched.next_date == out["next_due"]
    assert "auto=statement_cycle" in (sched.notes or "")
    assert "policy=statement" in (sched.notes or "")
    due = sched.next_date
    assert due.day == card.payment_due_day

    # Charge: ledger amount negative → credit owed increases by 50
    apply_amount_to_account(card, Decimal("-50.00"))
    s.flush()
    assert card.current_balance == Decimal("150.00")

    # Hook via object_session should have recomputed
    sched2 = _card_payment_schedule(s, card.id)
    assert sched2 is not None
    assert sched2.id == sched.id
    assert sched2.amount == Decimal("-150.00")
    assert sched2.next_date == due
    assert sched2.next_date.day == card.payment_due_day
    assert card.next_payment_amount_cached == Decimal("150.00")
    s.close()


def test_set_autopay_creates_cash_funded_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("250.00"), policy="none")
    as_of = date(2026, 8, 5)

    r = set_autopay(
        s, account_id=card.id, policy="statement", apply_schedule=True, as_of=as_of
    )
    s.commit()
    assert r["ok"] is True
    assert r["policy"] == "statement"
    # Reverse-sync: autopay_policy statement → payment_option statement
    s.refresh(card)
    assert card.autopay_policy == "statement"
    assert card.payment_option == "statement"
    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.amount == Decimal("-250.00")
    assert "Card payment ·" in sched.name
    # Legacy card-side Autopay · rows should not be active
    legacy = (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == card.id,
            ScheduledItem.name.like("Autopay ·%"),
        )
        .count()
    )
    assert legacy == 0
    s.close()


def test_policy_none_ends_card_payment_schedule(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    _p, _cash, card = _card_and_cash(s, bal=Decimal("80.00"))
    recompute_card_payment_schedule(s, card.id, as_of=date(2026, 9, 1))
    s.commit()
    assert _card_payment_schedule(s, card.id) is not None

    card.payment_option = "statement"
    s.flush()
    set_autopay(s, account_id=card.id, policy="none", apply_schedule=True)
    s.commit()
    assert _card_payment_schedule(s, card.id) is None
    # none leaves payment_option as-is (no invented wizard state)
    s.refresh(card)
    assert card.autopay_policy == "none"
    assert card.payment_option == "statement"
    s.close()


def test_cash_card_payment_counts_in_ifpp(tmp_path: Path, monkeypatch):
    """Cash-funded Card payment schedule must hit IFPP runway (not skipped)."""
    s = _session(tmp_path, monkeypatch)
    p, cash, card = _card_and_cash(s, bal=Decimal("300.00"), policy="statement")
    s.get(AppSettings, 1).safety_buffer = Decimal("0")
    recompute_card_payment_schedule(s, card.id, as_of=date.today())
    s.commit()

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.amount == Decimal("-300.00")

    # Baseline without the schedule (temporarily inactive) then with it
    sched.active = False
    s.flush()
    r_no = run_ifpp(s, profile_id=p.id)
    spendable_without = r_no.cash_spendable

    sched.active = True
    s.flush()
    r = run_ifpp(s, profile_id=p.id)
    # Must not treat cash Card payment as a credit-account schedule
    assert r.details.get("skipped_card_autopay_schedules", 0) == 0
    # Safe reduced by the cash card payment vs baseline without it
    assert r.cash_spendable <= spendable_without - Decimal("250")
    assert r.cash_spendable < spendable_without
    s.close()


def test_recompute_all_card_payments(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p, _cash, card = _card_and_cash(s, bal=Decimal("40.00"))
    n = recompute_all_card_payments(s, profile_id=p.id)
    s.commit()
    assert n >= 1
    assert card.next_payment_amount_cached == Decimal("40.00")
    s.close()


def test_after_account_balance_changed_noop_for_checking(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("500"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    out = after_account_balance_changed(s, cash.id)
    assert out.get("ok") is False or out.get("skipped") is True
    s.close()


def test_explicit_autopay_none_sticky_through_charge(tmp_path: Path, monkeypatch):
    """Explicit none must not be backfilled from payment_option on recompute.

    Card has payment_option=statement (or interest_saving) but user set
    autopay_policy=none. A charge must leave policy none and no Card payment
    schedule active.
    """
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("100.00"), policy="statement")
    card.payment_option = "statement"
    s.flush()

    # User explicitly turns autopay off
    set_autopay(s, account_id=card.id, policy="none", apply_schedule=True)
    s.commit()
    assert card.autopay_policy == "none"
    assert _card_payment_schedule(s, card.id) is None

    # Charge via apply_amount_to_account (triggers recompute hook)
    apply_amount_to_account(card, Decimal("-50.00"))
    s.flush()
    assert card.current_balance == Decimal("150.00")
    assert card.autopay_policy == "none"
    assert _card_payment_schedule(s, card.id) is None

    # interest_saving path too
    card.payment_option = "interest_saving"
    card.autopay_policy = "none"
    apply_amount_to_account(card, Decimal("-10.00"))
    s.flush()
    assert card.autopay_policy == "none"
    assert _card_payment_schedule(s, card.id) is None
    s.close()


def test_missing_funding_ends_stale_cash_schedule(tmp_path: Path, monkeypatch):
    """Active policy + null funding ends existing card-tagged cash schedules."""
    s = _session(tmp_path, monkeypatch)
    _p, cash, card = _card_and_cash(s, bal=Decimal("120.00"), policy="statement")
    as_of = date.today()
    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out["ok"] is True
    assert _card_payment_schedule(s, card.id) is not None

    card.payment_funding_account_id = None
    s.flush()
    out2 = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert out2.get("schedule", {}).get("ok") is False
    assert "funding" in (out2.get("schedule") or {}).get("error", "").lower()
    assert (out2.get("schedule") or {}).get("ended") is True
    assert _card_payment_schedule(s, card.id) is None
    # Caches may still reflect projection
    assert card.next_payment_amount_cached is not None
    s.close()


def test_archive_credit_ends_card_payment_schedule(tmp_path: Path, monkeypatch):
    """POST archive on credit ends cash Card payment schedules for that card."""
    import financial_os.api.app as app_mod
    from fastapi.testclient import TestClient
    from financial_os.db import init_db, make_engine, make_session_factory

    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with app_mod.SessionLocal() as s:
        p, cash, card = _card_and_cash(s, bal=Decimal("175.00"), policy="statement")
        recompute_card_payment_schedule(s, card.id, as_of=date.today())
        s.commit()
        card_id = card.id
        assert _card_payment_schedule(s, card_id) is not None
        assert card.next_payment_amount_cached == Decimal("175.00")

    with TestClient(app_mod.app) as client:
        r = client.post(f"/api/accounts/{card_id}/archive")
        assert r.status_code == 200, r.text
        assert r.json().get("archived") is True

    with app_mod.SessionLocal() as s:
        assert _card_payment_schedule(s, card_id) is None
        card = s.get(Account, card_id)
        assert card is not None
        assert card.archived_at is not None
        assert card.next_payment_amount_cached is None
        assert card.statement_balance_cached is None
        assert card.next_payment_date_cached is None


def test_promo_sink_suggested_matches_open_line_monthly(tmp_path: Path, monkeypatch):
    """promo_sink suggested_amount uses open_promo_totals monthly (not balloon/months)."""
    s = _session(tmp_path, monkeypatch)
    as_of = date(2026, 6, 10)
    _p, cash, card = _card_and_cash(s, bal=Decimal("1800.00"), policy="promo_sink")
    card.promo_balance = Decimal("1800")
    card.promo_end_date = date(2026, 12, 10)  # ~6 months → balloon/mo would be ~300
    s.flush()
    create_promo_line(
        s,
        card.id,
        name="Fridge 0%",
        principal_remaining=Decimal("1800"),
        monthly_payment=Decimal("75"),
        start_date=as_of - timedelta(days=30),
        end_date=date(2026, 12, 10),
    )
    s.commit()

    _principal, monthly = open_promo_totals(s, card.id, as_of=as_of)
    assert monthly == Decimal("75.00")

    sug = _suggested_amount(card, "promo_sink", session=s, as_of=as_of)
    assert sug == Decimal("75.00")

    proj = project_card_payment(s, card.id, as_of=as_of)
    assert Decimal(str(proj["next_payment"])) == Decimal("75.00")
    assert Decimal(str(proj["promo_due"])) == Decimal("75.00")

    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert Decimal(str(out["next_payment"])) == Decimal("75.00")
    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.name == "Card payment · Visa"
    assert sched.amount == Decimal("-75.00")

    items = list_autopay(s)["items"]
    row = next(i for i in items if i["account_id"] == card.id)
    assert Decimal(row["suggested_amount"]) == Decimal("75.00")
    s.close()


def test_promo_sink_with_funding_does_not_create_card_side_sink(
    tmp_path: Path, monkeypatch
):
    """set_autopay promo_sink + funding → cash Card payment; never card-side 0% sink."""
    s = _session(tmp_path, monkeypatch)
    as_of = date(2026, 7, 5)
    _p, cash, card = _card_and_cash(s, bal=Decimal("1200.00"), policy="none")
    create_promo_line(
        s,
        card.id,
        name="Sofa",
        principal_remaining=Decimal("1200"),
        monthly_payment=Decimal("100"),
        start_date=as_of - timedelta(days=5),
    )
    s.commit()

    r = set_autopay(
        s, account_id=card.id, policy="promo_sink", apply_schedule=True, as_of=as_of
    )
    s.commit()
    assert r["ok"] is True
    assert r["policy"] == "promo_sink"
    assert Decimal(r["suggested_amount"]) == Decimal("100.00")

    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert "Card payment ·" in sched.name
    assert sched.amount == Decimal("-100.00")

    card_side_sink = (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == card.id,
            ScheduledItem.name.like("0% sink ·%"),
        )
        .count()
    )
    assert card_side_sink == 0
    s.close()


def test_promo_sink_no_funding_can_use_legacy_card_sink(tmp_path: Path, monkeypatch):
    """Without funding, set_autopay promo_sink may create legacy card-side 0% sink."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date.today()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="NoFund Card",
        current_balance=Decimal("3000"),
        credit_limit=Decimal("10000"),
        available_credit=Decimal("7000"),
        promo_apr=Decimal("0"),
        promo_end_date=as_of + timedelta(days=90),
        promo_balance=Decimal("3000"),
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy="none",
        payment_funding_account_id=None,
        is_cash_for_ifpp=False,
    )
    s.add(card)
    s.commit()

    r = set_autopay(
        s, account_id=card.id, policy="promo_sink", apply_schedule=True, as_of=as_of
    )
    s.commit()
    assert r["ok"] is True
    # Recompute ends with no funding; fallback creates 0% sink on card
    assert r.get("schedule") and r["schedule"].get("ok") is True
    sink = (
        s.query(ScheduledItem)
        .filter(
            ScheduledItem.active.is_(True),
            ScheduledItem.account_id == card.id,
            ScheduledItem.name.like("0% sink ·%"),
        )
        .first()
    )
    assert sink is not None
    assert sink.amount < 0
    s.close()


def test_promo_sink_suggested_falls_back_to_balloon_months(
    tmp_path: Path, monkeypatch
):
    """With no open lines, suggested uses effective_promo_balance / months-to-end."""
    s = _session(tmp_path, monkeypatch)
    as_of = date(2026, 1, 1)
    _p, _cash, card = _card_and_cash(s, bal=Decimal("1200.00"), policy="promo_sink")
    card.promo_balance = Decimal("1200")
    card.promo_end_date = date(2026, 5, 1)  # 120 days → 4 months
    s.commit()

    sug = _suggested_amount(card, "promo_sink", session=s, as_of=as_of)
    # 1200 / 4 = 300
    assert sug == Decimal("300.00")
    s.close()


def test_payment_option_alias_does_not_clobber_sticky_none(tmp_path: Path, monkeypatch):
    """API alias only maps payment_option when autopay_policy is blank.

    Explicit none (and any other set policy) must stay sticky — same contract as
    ensure_autopay_policy_from_payment_option.
    """
    from financial_os.api.app import _apply_payment_option_alias

    s = _session(tmp_path, monkeypatch)
    _p, _cash, card = _card_and_cash(s, bal=Decimal("100.00"), policy="none")
    card.payment_option = "statement"
    card.autopay_policy = "none"
    s.flush()

    _apply_payment_option_alias(card)
    assert card.autopay_policy == "none"

    # Other explicit policies also stick
    card.autopay_policy = "min"
    card.payment_option = "interest_saving"
    _apply_payment_option_alias(card)
    assert card.autopay_policy == "min"

    # Null/blank policy still backfills from wizard alias
    card.autopay_policy = None
    card.payment_option = "statement"
    _apply_payment_option_alias(card)
    assert card.autopay_policy == "statement"

    card.autopay_policy = ""
    card.payment_option = "minimum"
    _apply_payment_option_alias(card)
    assert card.autopay_policy == "min"
    s.close()


def test_statement_suggested_carves_out_open_promo(tmp_path: Path, monkeypatch):
    """statement suggested_amount = max(0, bal - promo_remaining) + promo_due.

    Matches project_card_payment / compute_next_payment and the cash schedule
    amount so list_autopay stays honest with open promo lines.
    books remains full balance.
    """
    s = _session(tmp_path, monkeypatch)
    # Use today so list_autopay (session only, as_of=today) sees the same open lines.
    as_of = date.today()
    # bal 1000, promo remaining 400, monthly due 50 → statement pay 650
    _p, cash, card = _card_and_cash(s, bal=Decimal("1000.00"), policy="statement")
    create_promo_line(
        s,
        card.id,
        name="TV 0%",
        principal_remaining=Decimal("400"),
        monthly_payment=Decimal("50"),
        start_date=as_of - timedelta(days=30),
        end_date=as_of + timedelta(days=180),
    )
    s.commit()

    promo_rem, promo_due = open_promo_totals(s, card.id, as_of=as_of)
    assert promo_rem == Decimal("400.00")
    assert promo_due == Decimal("50.00")
    expected = Decimal("650.00")  # max(0, 1000-400) + 50

    sug = _suggested_amount(card, "statement", session=s, as_of=as_of)
    assert sug == expected

    # books ignores carve-out
    assert _suggested_amount(card, "books", session=s, as_of=as_of) == Decimal(
        "1000.00"
    )

    # Without session, statement falls back to full balance
    assert _suggested_amount(card, "statement", session=None, as_of=as_of) == Decimal(
        "1000.00"
    )

    proj = project_card_payment(s, card.id, as_of=as_of)
    assert Decimal(str(proj["next_payment"])) == expected
    assert Decimal(str(proj["statement_balance"])) == Decimal("600.00")

    out = recompute_card_payment_schedule(s, card.id, as_of=as_of)
    s.commit()
    assert Decimal(str(out["next_payment"])) == expected
    sched = _card_payment_schedule(s, card.id)
    assert sched is not None
    assert sched.account_id == cash.id
    assert sched.amount == -expected

    items = list_autopay(s)["items"]
    row = next(i for i in items if i["account_id"] == card.id)
    assert row["policy"] == "statement"
    assert Decimal(row["suggested_amount"]) == expected
    s.close()
