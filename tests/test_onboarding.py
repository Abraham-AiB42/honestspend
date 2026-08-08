from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from financial_os.config import settings
from financial_os.db import Account, AppSettings, init_db
from financial_os.seed import seed_all
from financial_os.services.onboarding import (
    apply_quick_setup,
    complete_onboarding,
    get_onboarding_status,
)


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    # Align SQLAlchemy engine with FOS data dir
    engine = create_engine(f"sqlite:///{(data / 'financial_os.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    s.commit()
    return s


def test_fresh_needs_setup(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    st = get_onboarding_status(s)
    assert st.complete is False
    assert st.account_count == 0
    s.close()


def test_quick_setup_creates_cash_and_card(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    result = apply_quick_setup(
        s,
        cash_name="Canvas",
        cash_balance=Decimal("2500"),
        card_name="Amex",
        card_balance=Decimal("400"),
        card_limit=Decimal("8000"),
        card_due_day=28,
        card_close_day=5,
        card_promo_apr=Decimal("0"),
        card_promo_end="2027-01-15",
        safety_buffer=Decimal("200"),
    )
    s.commit()
    assert result["onboarding_complete"] is True
    assert "backup" in result or "backup_error" in result
    assert s.query(Account).count() == 2
    st = get_onboarding_status(s)
    assert st.complete is True
    assert st.has_cash_account is True
    assert st.has_credit_account is True
    settings_row = s.get(AppSettings, 1)
    assert Decimal(settings_row.safety_buffer) == Decimal("200")
    card = s.query(Account).filter(Account.kind == "credit").one()
    assert card.payment_due_day == 28
    assert card.promo_end_date is not None
    s.close()


def test_complete_without_accounts(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    complete_onboarding(s)
    s.commit()
    assert get_onboarding_status(s).complete is True
    s.close()
