"""Credit profile persistence for educational score inputs."""

from decimal import Decimal

from financial_os.config import settings
from financial_os.db import AppSettings, init_db, make_engine, make_session_factory
from financial_os.services.debt_service import get_credit_profile, run_credit_health


def test_credit_profile_roundtrip(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)

    eng = make_engine()
    init_db(eng)
    Session = make_session_factory(eng)

    with Session() as s:
        s.add(AppSettings(id=1))
        s.commit()

    with Session() as s:
        row = s.get(AppSettings, 1)
        assert row is not None
        row.credit_on_time_rate = Decimal("0.95")
        row.credit_late_30 = 1
        row.credit_hard_inquiries = 2
        row.credit_reported_vantage = 720
        s.commit()

    with Session() as s:
        p = get_credit_profile(s)
        assert p.on_time_rate == Decimal("0.95")
        assert p.late_30_12m == 1
        assert p.hard_inquiries_12m == 2
        assert p.reported_vantage == 720
        health = run_credit_health(s)
        assert "score" in health
        assert health.get("your_reported_vantage") == 720
