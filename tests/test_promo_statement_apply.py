"""Apply statement promo terms on import; recompute next payment (Task 5)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Profile, PromoInstallmentLine, init_db
from honestspend.seed import seed_all
from honestspend.services.import_bootstrap import apply_statement_promos
from honestspend.services.statement_cycle import project_card_payment

AMAZON_ISB = """
Interest Saving Balance
Plan                  Remaining    Monthly payment    Payments left
Amazon Mixmaster      $348.12      $29.01             12
"""


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


def test_apply_statement_promos_amazon_increases_next_payment_by_monthly(
    tmp_path: Path, monkeypatch
):
    """PDF/text apply on a card: next payment includes Amazon monthly (policy statement).

    statement pay = max(0, bal − promo_remaining) + monthly → carve-out then +monthly.
    """
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 8, 12)
    bal = Decimal("1000.00")
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Amazon Card",
        current_balance=bal,
        credit_limit=Decimal("5000"),
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()

    before = project_card_payment(s, card.id, as_of=as_of)
    assert Decimal(str(before["next_payment"])) == bal

    out = apply_statement_promos(s, card.id, AMAZON_ISB, as_of=as_of)
    s.commit()

    assert out["created"] >= 1
    assert out["conflicts"] == 0 or out["conflicts"] == []
    lines = s.query(PromoInstallmentLine).filter(PromoInstallmentLine.account_id == card.id).all()
    assert any(ln.name == "Amazon Mixmaster" for ln in lines)

    after = project_card_payment(s, card.id, as_of=as_of)
    # (1000 - 348.12) + 29.01 = 680.89 — monthly is the increase vs pure carve-out
    expected = (bal - Decimal("348.12") + Decimal("29.01")).quantize(Decimal("0.01"))
    assert Decimal(str(after["next_payment"])) == expected
    pure_carve = (bal - Decimal("348.12")).quantize(Decimal("0.01"))
    assert Decimal(str(after["next_payment"])) == pure_carve + Decimal("29.01")

    s.close()


APPLE_ONE_PLAN = """
07/31/2026 MONTHLY INSTALLMENTS (11 OF 24) $41.62
06/30/2026 MONTHLY INSTALLMENTS (10 OF 24) $41.62
"""


def test_apply_apple_monthly_installments_like_amazon_isb(tmp_path: Path, monkeypatch):
    """Apple N-of-M activity is a purchase plan: carve-out remaining + add monthly."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    as_of = date(2026, 8, 12)
    bal = Decimal("1000.00")
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Apple Card",
        current_balance=bal,
        credit_limit=Decimal("5000"),
        payment_due_day=15,
        statement_close_day=1,
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()

    out = apply_statement_promos(s, card.id, APPLE_ONE_PLAN, as_of=as_of)
    s.commit()
    assert out["created"] >= 1
    lines = s.query(PromoInstallmentLine).filter(PromoInstallmentLine.account_id == card.id).all()
    assert any(
        ln.name == "Monthly installments · 24 mo"
        and ln.monthly_payment == Decimal("41.62")
        and ln.principal_remaining == Decimal("41.62") * 14
        for ln in lines
    )
    after = project_card_payment(s, card.id, as_of=as_of)
    remaining = Decimal("41.62") * 14
    expected = (bal - remaining + Decimal("41.62")).quantize(Decimal("0.01"))
    assert Decimal(str(after["next_payment"])) == expected
    s.close()


def test_apply_statement_promos_empty_text_no_op(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    card = Account(
        profile_id=p.id,
        kind="credit",
        nickname="Plain",
        current_balance=Decimal("200"),
        credit_limit=Decimal("1000"),
        payment_due_day=10,
        autopay_policy="statement",
    )
    s.add(card)
    s.flush()
    out = apply_statement_promos(s, card.id, "")
    assert out["created"] == 0
    assert out["lines"] == [] or out.get("updated", 0) == 0
    s.close()
