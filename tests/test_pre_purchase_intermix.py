from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, AppSettings, Profile, Transaction, init_db
from financial_os.seed import seed_all
from financial_os.services.intermix import apply_intermix
from financial_os.services.pre_purchase import check_purchase
from financial_os.services.promo_clock import promo_death_clock
from financial_os.services.digest import build_digest
from financial_os.services.permissions import Role, list_roles, default_context


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_pre_purchase_cash_safe(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("0")
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
        )
    )
    s.flush()
    res = check_purchase(s, amount=Decimal("500"), prefer="cash")
    assert res["verdict"] == "safe"
    assert res["recommended"]["safe"] is True
    s.close()


def test_pre_purchase_unsafe_when_broke(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("1000")
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Ops",
            current_balance=Decimal("100"),
            is_cash_for_ifpp=True,
        )
    )
    s.flush()
    res = check_purchase(s, amount=Decimal("500"), prefer="cash")
    assert res["recommended"]["safe"] is False
    s.close()


def test_promo_clock_sinking_fund(tmp_path: Path):
    s = _session(tmp_path)
    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    s.add(
        Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Discover 0%",
            current_balance=Decimal("3000"),
            credit_limit=Decimal("10000"),
            available_credit=Decimal("7000"),
            payment_due_day=8,
            statement_close_day=12,
            promo_apr=Decimal("0"),
            promo_end_date=date.today() + timedelta(days=90),
            promo_balance=Decimal("3000"),
            min_payment=Decimal("75"),
        )
    )
    s.flush()
    clock = promo_death_clock(s)
    assert clock["count"] == 1
    assert clock["items"][0]["urgency"] in ("watch", "soon", "ok", "critical")
    assert Decimal(clock["items"][0]["sinking_fund"]["monthly"]) > 0
    s.close()


def test_intermix_capital_inject(tmp_path: Path):
    s = _session(tmp_path)
    from financial_os.services.profiles import create_profile

    personal = s.query(Profile).filter(Profile.slug == "personal").one()
    biz = create_profile(s, display_name="Demo Business", entity_type="business")
    s.flush()
    p_acct = Account(
        profile_id=personal.id,
        kind="checking",
        nickname="Personal",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    b_acct = Account(
        profile_id=biz.id,
        kind="checking",
        nickname="Business ops",
        current_balance=Decimal("100"),
        is_cash_for_ifpp=True,
    )
    s.add_all([p_acct, b_acct])
    s.flush()
    res = apply_intermix(
        s,
        kind="capital_inject",
        amount=Decimal("1000"),
        from_account_id=p_acct.id,
        to_account_id=b_acct.id,
    )
    assert res["ok"] is True
    s.refresh(p_acct)
    s.refresh(b_acct)
    assert p_acct.current_balance == Decimal("4000")
    assert b_acct.current_balance == Decimal("1100")
    assert s.query(Transaction).filter(Transaction.is_transfer.is_(True)).count() == 2
    s.close()


def test_digest_and_permissions(tmp_path: Path):
    s = _session(tmp_path)
    dig = build_digest(s)
    assert "headline" in dig
    assert "minutes_needed" in dig
    roles = list_roles()
    assert any(r["role"] == "owner" for r in roles)
    ctx = default_context()
    assert ctx.can("write_settings")
    assert ctx.role == Role.owner
    s.close()
