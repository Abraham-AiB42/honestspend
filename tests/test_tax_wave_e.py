"""Tax geo, readiness, CPA pack."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.db import Account, Profile, Transaction, init_db
from honestspend.seed import seed_all
from honestspend.services.cpa_pack import build_cpa_pack_zip
from honestspend.services.tax_packet import build_tax_packet, tax_packet_readiness


def _session(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_profile_geo_and_readiness(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    personal.home_state = "TX"
    personal.multi_state = True
    personal.state_allocation_json = json.dumps(
        [{"state": "TX", "pct": 70}, {"state": "CA", "pct": 30}]
    )
    personal.filing_notes = "S-corp K-1 TBD"
    s.commit()

    year = date.today().year
    # empty year → not ready
    r = tax_packet_readiness(s, profile_id=personal.id, year=year)
    assert r["ready"] is False
    assert any("No non-transfer" in i for i in r["issues"])

    acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add(acct)
    s.flush()
    s.add(
        Transaction(
            profile_id=personal.id,
            account_id=acct.id,
            txn_date=date(year, 3, 1),
            amount=Decimal("-50"),
            payee="Office supplies",
            status="cleared",
            is_transfer=False,
        )
    )
    s.commit()

    packet = build_tax_packet(s, profile_id=personal.id, year=year)
    assert packet["profile"]["home_state"] == "TX"
    assert packet["profile"]["multi_state"] is True
    assert packet["readiness"]["ready"] is True
    assert packet["profile"]["state_allocation"][0]["state"] == "TX"

    data, meta = build_cpa_pack_zip(s, profile_id=personal.id, year=year, issue_token=True)
    s.commit()
    assert meta["ok"] is True
    assert meta["token"] is not None
    assert meta["token"]["api_token"].startswith("lr_")
    assert data[:2] == b"PK"  # zip magic
    s.close()
