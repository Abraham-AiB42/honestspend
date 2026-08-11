"""Plaid trial Item limit (10 institutions)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import PlaidItem, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services import plaid_service as ps
from financial_os.services.secrets_store import PLAID_ITEM_TRIAL_LIMIT


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "d"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_assert_can_link_blocks_at_10(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    profile = s.query(Profile).filter(Profile.slug == "personal").one()
    for i in range(PLAID_ITEM_TRIAL_LIMIT):
        s.add(
            PlaidItem(
                profile_id=profile.id,
                item_id=f"item-{i}",
                access_token=f"access-{i}",
                institution_name=f"Bank {i}",
                status="active",
            )
        )
    s.commit()
    assert ps.item_count(s) == 10
    try:
        ps.assert_can_link_item(s)
        raised = False
    except ps.PlaidError as e:
        raised = True
        assert "10" in str(e) or "limit" in str(e).lower()
    assert raised
    # reconnect same item id ok
    ps.assert_can_link_item(s, item_id="item-0")
    s.close()
