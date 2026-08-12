"""Offer desk: verdict, decide (new card / BT / plan), and APIs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import (
    Account,
    AppSettings,
    Profile,
    PromoInstallmentLine,
    Transaction,
    init_db,
    make_engine,
    make_session_factory,
)
from honestspend.seed import seed_all
from honestspend.services.promo_upsert import upsert_promo_term


AS_OF = date(2026, 9, 1)


def _session(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    engine = create_engine(f"sqlite:///{(data / 't.db').as_posix()}")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_all(s)
    row = s.get(AppSettings, 1)
    row.safety_buffer = Decimal("0")
    row.budget_reserve_enabled = False
    s.commit()
    return s


def _checking(s, p, *, balance: Decimal, nickname: str = "Checking") -> Account:
    a = Account(
        profile_id=p.id,
        kind="checking",
        nickname=nickname,
        current_balance=balance,
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("0"),
    )
    s.add(a)
    s.flush()
    return a


def _card(
    s,
    p,
    *,
    nickname: str,
    due_day: int | None,
    balance: Decimal = Decimal("0"),
    limit: Decimal = Decimal("10000"),
    close_day: int | None = 1,
    policy: str = "statement",
    apr: Decimal | None = None,
    funding_id: int | None = None,
) -> Account:
    a = Account(
        profile_id=p.id,
        kind="credit",
        nickname=nickname,
        current_balance=balance,
        credit_limit=limit,
        available_credit=limit - balance,
        payment_due_day=due_day,
        statement_close_day=close_day,
        autopay_policy=policy,
        min_payment=Decimal("25"),
        apr=apr,
        payment_funding_account_id=funding_id,
    )
    s.add(a)
    s.flush()
    return a


def test_bt_fee_greater_than_interest_is_skip(tmp_path: Path, monkeypatch):
    """BT fee > interest saved → skip."""
    from honestspend.services.offers import create_offer, offer_verdict

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(s, p, nickname="0% dest", due_day=26, funding_id=cash.id)
    s.commit()

    # $1000 for 6 months @ 24% ≈ $120 interest; 15% fee = $150 > interest
    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Pricey BT",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("15"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()
    assert offer.kind == "offer"
    assert offer.status == "pending"

    v = offer_verdict(s, offer)
    assert v["verdict"] == "skip"
    s.close()


def test_bt_fee_less_than_interest_and_safe_is_take(tmp_path: Path, monkeypatch):
    """Opposite of expensive BT + Safe fits → take."""
    from honestspend.services.offers import create_offer, offer_verdict

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(s, p, nickname="0% dest", due_day=26, funding_id=cash.id)
    s.commit()

    # $1000 for 6 months @ 24% ≈ $120 interest; 3% fee = $30 < interest
    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Cheap BT",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("3"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()

    v = offer_verdict(s, offer)
    assert v["verdict"] == "take"
    s.close()


def test_offer_verdict_ranker_exception_is_skip(tmp_path: Path, monkeypatch):
    """rank_charge raising in offer_verdict fails closed to skip, not take."""
    from honestspend.services.offers import create_offer, offer_verdict

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(s, p, nickname="0% dest", due_day=26, funding_id=cash.id)
    s.commit()

    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Cheap BT",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("3"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()

    def _boom(*_a, **_k):
        raise RuntimeError("ranker exploded")

    monkeypatch.setattr("honestspend.services.offers.rank_charge", _boom)
    v = offer_verdict(s, offer)
    assert v["verdict"] == "skip"
    assert v["verdict"] != "take"
    s.close()


def test_new_card_accept_creates_account(tmp_path: Path, monkeypatch):
    from honestspend.services.offers import create_offer, decide_offer

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    _checking(s, p, balance=Decimal("5000"))
    s.commit()

    before = s.query(Account).filter(Account.kind == "credit").count()
    offer = create_offer(
        s,
        offer_type="new_card",
        name="Mailer Visa",
        credit_limit=Decimal("8000"),
        months=15,
        intro_apr=Decimal("0"),
        close_day=1,
        due_day=25,
        profile_id=p.id,
        start_date=AS_OF,
    )
    s.commit()
    assert offer.account_id is None
    assert offer.status == "pending"

    result = decide_offer(s, offer.id, decision="take")
    s.commit()

    s.refresh(offer)
    assert offer.status == "inactive"
    assert s.query(Account).filter(Account.kind == "credit").count() == before + 1
    new_id = result.get("account_id") or offer.account_id
    new_acct = s.get(Account, new_id)
    assert new_acct is not None
    assert new_acct.kind == "credit"
    assert new_acct.nickname == "Mailer Visa"
    intro = (
        s.query(PromoInstallmentLine)
        .filter(
            PromoInstallmentLine.account_id == new_acct.id,
            PromoInstallmentLine.kind == "card_intro",
            PromoInstallmentLine.status == "open",
        )
        .one()
    )
    assert intro.apr == Decimal("0")
    s.close()


def test_skip_sets_declined(tmp_path: Path, monkeypatch):
    from honestspend.services.offers import create_offer, decide_offer, offer_verdict

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(s, p, nickname="0% dest", due_day=26, funding_id=cash.id)
    s.commit()

    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Skip me",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("3"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()
    assert offer_verdict(s, offer)["verdict"] == "take"

    # skip without confirm_skip is still allowed when verdict is Take
    decide_offer(s, offer.id, decision="skip", confirm_skip=False)
    s.commit()
    s.refresh(offer)
    assert offer.status == "declined"
    s.close()


def test_take_bt_writes_fee_transfer_and_intro(tmp_path: Path, monkeypatch):
    from honestspend.services.offers import create_offer, decide_offer

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(s, p, nickname="0% dest", due_day=26, funding_id=cash.id)
    s.commit()

    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Take BT",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("3"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()

    decide_offer(s, offer.id, decision="take")
    s.commit()
    s.refresh(offer)
    s.refresh(src)
    s.refresh(dest)

    assert offer.status == "inactive"
    assert src.current_balance == Decimal("1000.00")  # 2000 − 1000
    assert dest.current_balance == Decimal("1030.00")  # principal + 3% fee
    fee_txns = (
        s.query(Transaction)
        .filter(Transaction.account_id == dest.id, Transaction.amount == Decimal("-30.00"))
        .all()
    )
    assert len(fee_txns) >= 1
    intro = (
        s.query(PromoInstallmentLine)
        .filter(
            PromoInstallmentLine.account_id == dest.id,
            PromoInstallmentLine.kind == "card_intro",
            PromoInstallmentLine.status == "open",
        )
        .one()
    )
    assert intro.principal_remaining == Decimal("1000.00")
    s.close()


def test_take_purchase_plan_creates_open_line(tmp_path: Path, monkeypatch):
    from honestspend.services.offers import create_offer, decide_offer

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    card = _card(s, p, nickname="Store card", due_day=26, funding_id=cash.id)
    s.commit()

    offer = create_offer(
        s,
        offer_type="purchase_plan",
        name="Fridge plan",
        account_id=card.id,
        amount=Decimal("600"),
        monthly=Decimal("50"),
        months=12,
        start_date=AS_OF,
    )
    s.commit()

    decide_offer(s, offer.id, decision="take")
    s.commit()
    s.refresh(offer)
    assert offer.status == "inactive"
    plan = (
        s.query(PromoInstallmentLine)
        .filter(
            PromoInstallmentLine.account_id == card.id,
            PromoInstallmentLine.kind == "purchase_plan",
            PromoInstallmentLine.status == "open",
        )
        .one()
    )
    assert plan.monthly_payment == Decimal("50.00")
    assert plan.principal_remaining == Decimal("600.00")
    s.close()


def test_take_with_plan_sets_policy_when_books(tmp_path: Path, monkeypatch):
    from honestspend.services.offers import create_offer, decide_offer, offer_verdict

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = _checking(s, p, balance=Decimal("5000"))
    src = _card(
        s, p, nickname="High APR", due_day=26, balance=Decimal("2000"), apr=Decimal("0.24")
    )
    dest = _card(
        s, p, nickname="Books dest", due_day=26, policy="books", funding_id=cash.id
    )
    s.commit()

    offer = create_offer(
        s,
        offer_type="balance_transfer",
        name="Plan BT",
        from_account_id=src.id,
        destination_account_id=dest.id,
        amount=Decimal("1000"),
        fee_pct=Decimal("3"),
        months=6,
        start_date=AS_OF,
    )
    s.commit()

    v = offer_verdict(s, offer)
    assert v["verdict"] == "take_with_plan"

    decide_offer(s, offer.id, decision="take_with_plan")
    s.commit()
    s.refresh(dest)
    assert dest.autopay_policy in ("statement", "promo_sink")
    s.close()


@contextmanager
def _api_client(tmp_path: Path, monkeypatch):
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
    with app_mod.SessionLocal() as sess:
        seed_all(sess)
        row = sess.get(AppSettings, 1)
        row.safety_buffer = Decimal("0")
        row.budget_reserve_enabled = False
        sess.commit()
    with TestClient(app_mod.app) as c:
        yield c


def test_api_offers_create_list_decide(tmp_path: Path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        profiles = client.get("/api/profiles").json()
        pid = next(p["id"] for p in profiles if p.get("slug") == "personal")
        cash = client.post(
            "/api/accounts",
            json={
                "profile_id": pid,
                "kind": "checking",
                "nickname": "Checking",
                "current_balance": "5000",
                "is_cash_for_ifpp": True,
            },
        ).json()
        src = client.post(
            "/api/accounts",
            json={
                "profile_id": pid,
                "kind": "credit",
                "nickname": "High APR",
                "current_balance": "2000",
                "credit_limit": "10000",
                "payment_due_day": 26,
                "statement_close_day": 1,
                "apr": "0.24",
            },
        ).json()
        dest = client.post(
            "/api/accounts",
            json={
                "profile_id": pid,
                "kind": "credit",
                "nickname": "0% dest",
                "current_balance": "0",
                "credit_limit": "10000",
                "payment_due_day": 26,
                "statement_close_day": 1,
            },
        ).json()

        created = client.post(
            "/api/offers",
            json={
                "offer_type": "balance_transfer",
                "name": "API BT",
                "from_account_id": src["id"],
                "destination_account_id": dest["id"],
                "amount": "1000",
                "fee_pct": "3",
                "months": 6,
                "start_date": AS_OF.isoformat(),
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        offer_id = body["id"]
        assert body["kind"] == "offer"
        assert body["status"] == "pending"

        listed = client.get("/api/offers")
        assert listed.status_code == 200
        listed_body = listed.json()
        items = (
            listed_body["items"]
            if isinstance(listed_body, dict) and "items" in listed_body
            else listed_body
        )
        assert any(int(it["id"]) == int(offer_id) for it in items)

        skipped = client.post(f"/api/offers/{offer_id}/decide", json={"decision": "skip"})
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["offer"]["status"] == "declined"


def test_api_promo_conflicts_list_and_resolve(tmp_path: Path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        from honestspend.services import db_runtime

        Session = db_runtime.session_factory()
        with Session() as s:
            p = s.query(Profile).filter(Profile.slug == "personal").one()
            card = Account(
                profile_id=p.id,
                kind="credit",
                nickname="Conflict card",
                current_balance=Decimal("1000"),
                credit_limit=Decimal("10000"),
                autopay_policy="statement",
            )
            s.add(card)
            s.flush()
            upsert_promo_term(
                s,
                account_id=card.id,
                kind="purchase_plan",
                name="Amazon Mixmaster",
                principal_remaining=Decimal("400.00"),
                monthly_payment=Decimal("29.01"),
                start_date=date(2026, 8, 1),
                end_date=None,
                source="user",
            )
            r = upsert_promo_term(
                s,
                account_id=card.id,
                kind="purchase_plan",
                name="Amazon Mixmaster",
                principal_remaining=Decimal("348.12"),
                monthly_payment=Decimal("29.01"),
                start_date=date(2026, 8, 1),
                end_date=None,
                source="statement",
            )
            s.commit()
            conflict_id = r["conflict"]["id"]

        listed = client.get("/api/promos/conflicts")
        assert listed.status_code == 200, listed.text
        payload = listed.json()
        items = payload["items"] if isinstance(payload, dict) and "items" in payload else payload
        assert any(int(it["id"]) == int(conflict_id) for it in items)

        resolved = client.post(
            f"/api/promos/conflicts/{conflict_id}/resolve",
            json={"action": "take_incoming"},
        )
        assert resolved.status_code == 200, resolved.text
        line = resolved.json().get("line") or resolved.json()
        assert line["principal_remaining"] in ("348.12", "348.1200")

        listed2 = client.get("/api/promos/conflicts")
        payload2 = listed2.json()
        items2 = payload2["items"] if isinstance(payload2, dict) and "items" in payload2 else payload2
        assert all(int(it["id"]) != int(conflict_id) for it in items2)
