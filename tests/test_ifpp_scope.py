"""Entity-siloed IFPP vs group scope."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, Profile, init_db
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.profiles import create_profile


def _session(tmp_path: Path):
    eng = create_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    init_db(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_entity_scope_isolates_cash(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    biz = create_profile(s, display_name="Demo Business", entity_type="business")
    s.commit()

    s.add(
        Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Personal checking",
            current_balance=Decimal("5000"),
            is_cash_for_ifpp=True,
        )
    )
    s.add(
        Account(
            profile_id=biz.id,
            kind="checking",
            nickname="Biz checking",
            current_balance=Decimal("90000"),
            is_cash_for_ifpp=True,
        )
    )
    s.commit()

    as_of = date(2026, 8, 8)
    # entity personal: only 5000 cash (minus buffer 1000 default)
    r_p = run_ifpp(s, as_of=as_of, profile_id=personal.id, scope="entity")
    assert r_p.details["ifpp_scope"] == "entity"
    assert r_p.details["profile_id"] == personal.id
    # 5000 - 1000 buffer = 4000 if no bills
    assert r_p.cash_spendable == Decimal("4000.00")

    r_b = run_ifpp(s, as_of=as_of, profile_id=biz.id, scope="entity")
    assert r_b.details["profile_id"] == biz.id
    assert r_b.cash_spendable == Decimal("89000.00")

    r_g = run_ifpp(s, as_of=as_of, scope="group")
    assert r_g.details["ifpp_scope"] == "group"
    # combined cash 95000 - 1000 buffer
    assert r_g.cash_spendable == Decimal("94000.00")

    s.close()
