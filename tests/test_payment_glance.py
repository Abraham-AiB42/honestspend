"""Payment matcher + glance API."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from honestspend.config import settings
from honestspend.db import Account, Transaction, init_db, make_engine, make_session_factory
from honestspend.seed import seed_all
from honestspend.services.payment_match import find_payment_candidates


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "require_api_key", False)
    monkeypatch.setattr(settings, "allow_non_loopback", False)

    import honestspend.api.app as app_mod

    app_mod.engine = make_engine()
    app_mod.SessionLocal = make_session_factory(app_mod.engine)
    init_db(app_mod.engine)
    with app_mod.SessionLocal() as s:
        seed_all(s)
        s.commit()

    with TestClient(app_mod.app) as c:
        yield c


def test_glance_shape(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 2500})
    r = client.get("/api/glance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "cash_spendable" in body
    assert "safe_to_spend" in body  # Home parity (post budget reserve)
    assert "budget_reserve" in body
    assert "alerts" in body
    assert "pending" in body
    assert body.get("client_hint")


def test_auto_link_payment_to_named_account(tmp_path: Path):
    from honestspend.db import Profile
    from honestspend.services.payment_match import auto_link_account_payments

    eng = make_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(Profile).filter_by(slug="personal").one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="AP Agency Checking",
            current_balance=Decimal("20000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="AiB42 Amex",
            institution="Blue Business Plus",
            current_balance=Decimal("11039.90"),
        )
        fake = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Payment to American Express",
            current_balance=Decimal("0"),
        )
        s.add_all([cash, card, fake])
        s.flush()
        out = Transaction(
            profile_id=personal.id,
            account_id=cash.id,
            txn_date=date(2025, 8, 11),
            amount=Decimal("-11039.90"),
            payee="Payment to American Express",
        )
        inn = Transaction(
            profile_id=personal.id,
            account_id=card.id,
            txn_date=date(2025, 8, 8),
            amount=Decimal("11039.90"),
            payee="ONLINE PAYMENT - THANK YOU",
        )
        s.add_all([out, inn])
        s.commit()
        res = auto_link_account_payments(s, as_of=date(2025, 8, 20))
        s.commit()
        assert res["linked"] == 1
        s.refresh(out)
        s.refresh(inn)
        assert out.is_transfer and inn.is_transfer
        assert out.transfer_pair_id == inn.id
        assert inn.account_id == card.id


def test_auto_link_pairs_already_flagged_transfers(tmp_path: Path):
    from honestspend.db import Profile
    from honestspend.services.payment_match import auto_link_account_payments

    eng = make_engine(f"sqlite:///{(tmp_path / 'flagged.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(Profile).filter_by(slug="personal").one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Checking",
            current_balance=Decimal("20000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="AP Agency Amex",
            institution="Blue Business Cash",
            current_balance=Decimal("4459.09"),
        )
        s.add_all([cash, card])
        s.flush()
        out = Transaction(
            profile_id=personal.id,
            account_id=cash.id,
            txn_date=date(2026, 8, 6),
            amount=Decimal("-4459.09"),
            payee="Payment to American Express",
            is_transfer=True,
        )
        inn = Transaction(
            profile_id=personal.id,
            account_id=card.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("4459.09"),
            payee="AUTOPAY PAYMENT - THANK YOU",
            is_transfer=True,
        )
        s.add_all([out, inn])
        s.commit()
        res = auto_link_account_payments(s, as_of=date(2026, 8, 20))
        s.commit()
        assert res["linked"] == 1
        s.refresh(out)
        s.refresh(inn)
        assert out.transfer_pair_id == inn.id


def test_auto_link_share_transfer_not_card(tmp_path: Path):
    from honestspend.db import Profile
    from honestspend.services.payment_match import auto_link_account_payments

    eng = make_engine(f"sqlite:///{(tmp_path / 'share.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(Profile).filter_by(slug="personal").one()
        agency = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="AP Agency Checking",
            current_balance=Decimal("20000"),
            is_cash_for_ifpp=True,
        )
        mm = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Canvas - MM",
            institution="Credit union",
            current_balance=Decimal("8000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="AiB42 Amex",
            institution="Blue Business Plus",
            current_balance=Decimal("5000"),
        )
        loan = Account(
            profile_id=personal.id,
            kind="loan",
            nickname="Palisade",
            institution="Hyundai Palisade",
            current_balance=Decimal("939.36"),
        )
        scheels = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Scheels",
            institution="Scheels",
            current_balance=Decimal("80"),
        )
        s.add_all([agency, mm, card, loan, scheels])
        s.flush()
        share_out = Transaction(
            profile_id=personal.id,
            account_id=agency.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("-5000"),
            payee="Withdrawal Transfer To Peters,Abraham XX7975 Share 02",
        )
        share_in = Transaction(
            profile_id=personal.id,
            account_id=mm.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("5000"),
            payee="Deposit Transfer From AP AGENCY 0001052521 Share 91",
        )
        # Same amount on the card the same day — must not steal the share pair.
        card_pay = Transaction(
            profile_id=personal.id,
            account_id=card.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("5000"),
            payee="ONLINE PAYMENT - THANK YOU",
        )
        hy_out = Transaction(
            profile_id=personal.id,
            account_id=mm.id,
            txn_date=date(2026, 8, 3),
            amount=Decimal("-939.36"),
            payee="Payment to Hyundai Motor Finance Company",
        )
        hy_in = Transaction(
            profile_id=personal.id,
            account_id=loan.id,
            txn_date=date(2026, 8, 1),
            amount=Decimal("939.36"),
            payee="Payment Received - Thank You!",
        )
        sch_out = Transaction(
            profile_id=personal.id,
            account_id=mm.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("-80.00"),
            payee="Payment to First Bankcard",
        )
        sch_ret = Transaction(
            profile_id=personal.id,
            account_id=scheels.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("80.00"),
            payee="PAYMENT REV - 1ST RETURN",
        )
        sch_in = Transaction(
            profile_id=personal.id,
            account_id=scheels.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("80.00"),
            payee="PAYMENT - THANK YOU",
        )
        check = Transaction(
            profile_id=personal.id,
            account_id=agency.id,
            txn_date=date(2026, 6, 3),
            amount=Decimal("-2000"),
            payee="Check # 116",
        )
        from_share = Transaction(
            profile_id=personal.id,
            account_id=mm.id,
            txn_date=date(2026, 6, 1),
            amount=Decimal("2000"),
            payee="From Share 01",
        )
        s.add_all([share_out, share_in, card_pay, hy_out, hy_in, sch_out, sch_ret, sch_in, check, from_share])
        s.commit()
        res = auto_link_account_payments(s, as_of=date(2026, 8, 20))
        s.commit()
        s.refresh(share_out)
        s.refresh(share_in)
        s.refresh(card_pay)
        s.refresh(hy_out)
        s.refresh(hy_in)
        s.refresh(sch_out)
        s.refresh(sch_ret)
        s.refresh(sch_in)
        s.refresh(check)
        s.refresh(from_share)
        assert share_out.transfer_pair_id == share_in.id
        assert card_pay.transfer_pair_id is None
        assert hy_out.transfer_pair_id == hy_in.id
        assert sch_out.transfer_pair_id == sch_in.id
        assert sch_ret.transfer_pair_id is None
        assert check.transfer_pair_id is None
        assert from_share.is_transfer is True
        assert check.is_transfer is False
        assert res["linked"] >= 3


def test_infer_share_and_skip_loan_for_bank(tmp_path: Path):
    from honestspend.db import Profile
    from honestspend.services.payment_match import infer_dest_account_name

    eng = make_engine(f"sqlite:///{(tmp_path / 'infer.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(Profile).filter_by(slug="personal").one()
        s.add_all(
            [
                Account(
                    profile_id=personal.id,
                    kind="checking",
                    nickname="Canvas Checking · …0001",
                    institution="Canvas Checking",
                    current_balance=Decimal("0"),
                ),
                Account(
                    profile_id=personal.id,
                    kind="savings",
                    nickname="Canvas Money market · …0002",
                    institution="Canvas Money market",
                    current_balance=Decimal("0"),
                ),
                Account(
                    profile_id=personal.id,
                    kind="checking",
                    nickname="AP Agency Checking",
                    current_balance=Decimal("100"),
                    is_cash_for_ifpp=True,
                ),
                Account(
                    profile_id=personal.id,
                    kind="credit",
                    nickname="Discover",
                    institution="Discover",
                    current_balance=Decimal("10"),
                ),
                Account(
                    profile_id=personal.id,
                    kind="loan",
                    nickname="Discover Loan",
                    institution="Discover",
                    current_balance=Decimal("0"),
                ),
            ]
        )
        s.commit()
        assert infer_dest_account_name(s, "From Share 01") == "Canvas Checking · …0001"
        assert infer_dest_account_name(s, "To Share 02") == "Canvas Money market · …0002"
        assert infer_dest_account_name(s, "Payment to Discover Bank") == "Discover"


def test_payment_pair_detect(tmp_path: Path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    SF = make_session_factory(eng)
    with SF() as s:
        seed_all(s)
        personal = s.query(__import__("honestspend.db", fromlist=["Profile"]).Profile).filter_by(
            slug="personal"
        ).one()
        cash = Account(
            profile_id=personal.id,
            kind="checking",
            nickname="Checking",
            current_balance=Decimal("1000"),
            is_cash_for_ifpp=True,
        )
        card = Account(
            profile_id=personal.id,
            kind="credit",
            nickname="Card",
            current_balance=Decimal("200"),
            credit_limit=Decimal("5000"),
            payment_due_day=15,
        )
        s.add_all([cash, card])
        s.flush()
        # cash pays card: -200 from cash, +200 on card (reduces owed)
        t_cash = Transaction(
            profile_id=personal.id,
            account_id=cash.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("-200"),
            payee="CARD PAYMENT",
            status="cleared",
        )
        t_card = Transaction(
            profile_id=personal.id,
            account_id=card.id,
            txn_date=date(2026, 8, 5),
            amount=Decimal("200"),
            payee="PAYMENT THANK YOU",
            status="cleared",
        )
        s.add_all([t_cash, t_card])
        s.commit()
        cand = find_payment_candidates(s, days=14, as_of=date(2026, 8, 10))
        assert cand["count"] >= 1
        pair = next(c for c in cand["candidates"] if c["kind"] == "cash_to_card_pair")
        assert pair["cash_txn_id"] == t_cash.id
        assert pair["card_txn_id"] == t_card.id


def test_payment_confirm_api(client: TestClient):
    client.post(
        "/api/onboarding/quick-setup",
        json={"cash_balance": 2000, "card_name": "C", "card_limit": 3000, "card_due_day": 15},
    )
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    card = next(a for a in accts if a["kind"] == "credit")
    # allow negative would need confirm — amount within balance
    r1 = client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-05",
            "amount": "-100",
            "payee": "CARD PAYMENT",
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/transactions",
        json={
            "profile_id": card["profile_id"],
            "account_id": card["id"],
            "txn_date": "2026-08-05",
            "amount": "100",
            "payee": "PAYMENT",
        },
    )
    assert r2.status_code == 200, r2.text
    conf = client.post(
        "/api/payments/confirm",
        json={"cash_txn_id": r1.json()["id"], "card_txn_id": r2.json()["id"]},
    )
    assert conf.status_code == 200, conf.text
    assert conf.json().get("ok") is True


def test_pending_on_ifpp(client: TestClient):
    client.post("/api/onboarding/quick-setup", json={"cash_balance": 5000})
    accts = client.get("/api/accounts").json()
    cash = next(a for a in accts if a["kind"] == "checking")
    client.post(
        "/api/transactions",
        json={
            "profile_id": cash["profile_id"],
            "account_id": cash["id"],
            "txn_date": "2026-08-06",
            "amount": "-75",
            "payee": "PENDING SHOP",
            "status": "pending",
        },
    )
    ifpp = client.get("/api/ifpp").json()
    assert int(ifpp.get("pending_count") or 0) >= 1
    assert ifpp.get("pending_warning")
