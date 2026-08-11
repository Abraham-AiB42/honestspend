"""Envelope raid: liquidity ok, Safe short — explicit allow flag."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, Category, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.budget_service import create_rule
from financial_os.services.pre_purchase import check_purchase


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


def test_envelope_raid_offer_and_allow(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    row.budget_reserve_enabled = True
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("2000"),
            is_cash_for_ifpp=True,
        )
    )
    cat = Category(
        code="TEST_RAID_FOOD",
        display_name="Food Raid",
        scope="personal",
        profile_id=p.id,
        budget_group="food",
    )
    s.add(cat)
    s.flush()
    create_rule(
        s,
        profile_id=p.id,
        category_id=cat.id,
        period="monthly",
        amount=Decimal("1500"),
        name="Food",
    )
    s.commit()
    # $800 needs raid (safe ~500)
    res = check_purchase(s, amount=Decimal("800"), prefer="cash", profile_id=p.id)
    assert res["recommended"]["safe"] is False
    er = res.get("envelope_raid") or {}
    assert er.get("available") is True
    res2 = check_purchase(
        s,
        amount=Decimal("800"),
        prefer="cash",
        profile_id=p.id,
        allow_envelope_raid=True,
    )
    assert res2["recommended"]["safe"] is True
    assert res2["verdict"] == "safe_raid_envelope"
    s.close()
