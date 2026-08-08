from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.db import Account, AppSettings, AppUser, Profile, ScheduledItem, init_db
from financial_os.engine.ifpp import CashAccountView, compute_cash_spendable
from financial_os.seed import seed_all
from financial_os.services.ifpp_service import run_ifpp
from financial_os.services.permissions import Role, generate_api_token, resolve_context
from financial_os.services.tax_vault import adjust_tax_vault, get_tax_vault, set_tax_vault


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    return s


def test_tax_vault_reduces_spendable():
    as_of = date(2026, 8, 6)
    cash = [CashAccountView(1, "Checking", Decimal("5000"), True, "checking")]
    spend, _, warnings = compute_cash_spendable(
        cash,
        [],
        as_of=as_of,
        mode="conservative",
        safety_buffer=Decimal("1000"),
        tax_vault=Decimal("1500"),
        never_negative_scope="checking",
    )
    # 5000 - 1000 buffer - 1500 vault = 2500
    assert spend == Decimal("2500.00")
    assert any("Tax vault" in w for w in warnings)


def test_tax_vault_adjust(tmp_path: Path):
    s = _session(tmp_path)
    set_tax_vault(s, balance=Decimal("0"), enabled=True)
    adjust_tax_vault(s, Decimal("800"))
    v = get_tax_vault(s)
    assert Decimal(v["balance"]) == Decimal("800")
    adjust_tax_vault(s, Decimal("-200"))
    assert Decimal(get_tax_vault(s)["balance"]) == Decimal("600")
    s.close()


def test_income_cliff_haircuts_expected(tmp_path: Path):
    s = _session(tmp_path)
    settings = s.get(AppSettings, 1)
    settings.safety_buffer = Decimal("0")
    settings.tax_vault_enabled = False
    settings.tax_vault_balance = Decimal("0")
    settings.income_cliff_enabled = True
    settings.income_cliff_factor = Decimal("0.5")
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
    s.add(
        ScheduledItem(
            profile_id=personal.id,
            name="Commission",
            amount=Decimal("2000"),
            next_date=date.today() + timedelta(days=2),
            cadence="monthly",
            certainty="expected",
            kind="income",
            active=True,
        )
    )
    s.add(
        ScheduledItem(
            profile_id=personal.id,
            name="Bill",
            amount=Decimal("-500"),
            next_date=date.today() + timedelta(days=5),
            cadence="monthly",
            certainty="fixed",
            kind="expense",
            active=True,
        )
    )
    s.flush()
    # With 50% cliff: income 1000 expected weight 0.85 → still should not go red
    # vs full 2000
    r = run_ifpp(s, mode="expected")
    assert r.cash_spendable >= Decimal("0")
    s.close()


def test_api_token_resolve(tmp_path: Path):
    s = _session(tmp_path)
    token = generate_api_token()
    s.add(
        AppUser(
            username="cpa",
            display_name="CPA",
            role="cpa_viewer",
            api_token=token,
            active=True,
        )
    )
    s.flush()
    ctx = resolve_context(s, token)
    assert ctx.role == Role.cpa_viewer
    assert ctx.can("tax_export")
    assert not ctx.can("write_settings")
    try:
        resolve_context(s, "lr_bad")
        assert False, "should fail"
    except PermissionError:
        pass
    s.close()
