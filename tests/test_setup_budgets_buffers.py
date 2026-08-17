"""Setup budgets seed review + dual buffers + IFPP effective buffer."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from honestspend.config import settings
from honestspend.db import Account, Category, Profile, Transaction, init_db
from honestspend.engine.ifpp import CashAccountView, compute_cash_spendable
from honestspend.seed import seed_all
from honestspend.services.ifpp_service import run_ifpp
from honestspend.services.pre_purchase import check_purchase
from honestspend.db import ScheduledItem
from honestspend.services.budget_service import infer_budget_cadence, move_budget_category, period_label
from honestspend.services.setup_budgets import apply_budget_edits, budgets_review, buffers_status, save_buffers


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


def test_effective_buffer_max_of_total_and_per_account():
    accounts = [
        CashAccountView(1, "A", Decimal("2000"), True, "checking", Decimal("500")),
        CashAccountView(2, "B", Decimal("1000"), True, "checking", Decimal("200")),
    ]
    # total floor 300, per-sum 700 → effective 700
    cash, _, _, _ = compute_cash_spendable(
        accounts,
        [],
        as_of=date(2026, 8, 10),
        mode="conservative",
        safety_buffer=Decimal("300"),
        horizon_days=14,
    )
    # 3000 - 700 = 2300
    assert cash == Decimal("2300")


def test_save_buffers_and_ifpp(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    a = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
        safety_buffer=Decimal("800"),
    )
    s.add(a)
    s.commit()
    save_buffers(s, total_buffer=Decimal("1000"), account_buffers=[{"id": a.id, "safety_buffer": 800}])
    s.commit()
    st = buffers_status(s, profile_id=p.id)
    assert Decimal(st["effective_buffer"]) == Decimal("1000")  # max(1000, 800)
    ifpp = run_ifpp(s, profile_id=p.id)
    # 5000 - 1000 buffer = 4000 (no bills)
    assert ifpp.cash_spendable == Decimal("4000.00")
    s.close()


def test_pre_purchase_lists_cash_accounts(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    settings_row = s.get(__import__("honestspend.db", fromlist=["AppSettings"]).AppSettings, 1)
    settings_row.safety_buffer = Decimal("0")
    s.add(
        Account(
            profile_id=p.id,
            kind="checking",
            nickname="Primary",
            current_balance=Decimal("3000"),
            is_cash_for_ifpp=True,
            safety_buffer=Decimal("500"),
        )
    )
    s.commit()
    res = check_purchase(s, amount=Decimal("100"), prefer="cash", profile_id=p.id)
    cash_opts = [o for o in res["options"] if o["method"] == "cash" and o.get("account_id")]
    assert cash_opts
    assert cash_opts[0]["safe"] is True
    s.close()


def test_budgets_review_seeds(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("4000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    cat = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    assert cat
    for i in range(16):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=cat.id,
                txn_date=date.today() - timedelta(days=i * 2),
                amount=Decimal("-25"),
                payee="Spend",
            )
        )
    s.commit()
    rev = budgets_review(s, profile_id=p.id, seed_if_empty=True)
    assert rev.get("lookback_message")
    assert "We analyzed spending from" in (rev.get("lookback_message") or "")
    assert rev.get("analyzed_from")
    assert rev.get("analyzed_from_label")
    assert rev["count"] >= 1
    assert any(r.get("period_label") for r in rev.get("rules") or [])
    assert any(r.get("amount_display", "").startswith("$") for r in rev.get("rules") or [])
    why_rules = [r for r in (rev.get("rules") or []) if r.get("why")]
    assert why_rules
    assert why_rules[0]["recommended"] is True
    assert why_rules[0]["why"].get("lines")
    assert "How we recommended" in (why_rules[0]["why"].get("title") or "")
    s.close()


def test_infer_cadence_workday_weekly_yearly():
    start = date(2026, 6, 1)
    workdays = [start + timedelta(days=i) for i in range(20) if (start + timedelta(days=i)).weekday() < 5]
    work = infer_budget_cadence(workdays[:10])
    assert work["period"] == "daily"
    assert period_label("daily", active_weekdays=work["active_weekdays"]) == "Per workday"

    weekly = infer_budget_cadence([start + timedelta(days=7 * i) for i in range(8)])
    assert weekly["period"] == "weekly"
    assert period_label("weekly") == "Weekly"

    yearly = infer_budget_cadence([date(2026, 1, 15), date(2026, 7, 20)])
    assert yearly["period"] == "yearly"
    assert period_label("yearly") == "Annual"

    bi = infer_budget_cadence([start + timedelta(days=14 * i) for i in range(6)])
    assert bi["period"] == "biweekly"
    assert period_label("biweekly") == "Bi-weekly"


def test_apply_can_create_and_delete_budget(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cat = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    assert cat
    created = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": cat.id,
                "period": "monthly",
                "amount": "$75.00",
                "name": cat.display_name,
            }
        ],
    )
    assert created["count"] == 1
    rid = created["changed"][0]["id"]
    yearly = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": cat.id,
                "period": "yearly",
                "amount": "1200",
                "name": f"{cat.display_name} annual",
            }
        ],
    )
    assert yearly["count"] == 1
    dropped = apply_budget_edits(s, updates=[{"id": rid, "delete": True}])
    assert dropped["changed"][0].get("dropped") is True
    still = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    assert still["count"] == 1
    assert still["rules"][0]["period_label"] == "Annual"
    s.close()


def test_budget_line_lists_and_moves_categories(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .order_by(Category.id)
        .all()
    )
    assert len(food) >= 2
    a, b = food[0], food[1]
    first = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": a.id,
                "period": "monthly",
                "amount": "100",
                "name": a.display_name,
            },
            {
                "create": True,
                "profile_id": p.id,
                "category_id": b.id,
                "period": "monthly",
                "amount": "40",
                "name": b.display_name,
            },
        ],
    )
    assert first["count"] == 2
    src_id = first["changed"][0]["id"]
    dest_id = first["changed"][1]["id"]
    rev = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    assert all(r.get("member_categories") for r in rev["rules"])
    moved = apply_budget_edits(
        s,
        updates=[{"id": src_id, "move_category_id": a.id, "to_rule_id": dest_id}],
    )
    assert moved["changed"][0].get("moved") is True
    rev2 = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    assert rev2["count"] == 1
    names = {c["name"] for c in rev2["rules"][0]["member_categories"]}
    assert a.display_name in names
    assert b.display_name in names
    dest = next(r for r in rev2["rules"])
    split = move_budget_category(
        s, from_rule_id=int(dest["id"]), category_id=int(a.id), to_new=True
    )
    assert split.get("moved") is True
    rev3 = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    assert rev3["count"] == 2
    s.close()


def test_large_budget_shows_recent_txns_and_recalc(tmp_path: Path, monkeypatch):
    from honestspend.services.profiles import create_profile

    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    biz = create_profile(s, display_name="Studio LLC", entity_type="business")
    food = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.budget_group == "food")
        .first()
    )
    shop = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.display_name.ilike("%shop%"))
        .first()
    )
    assert food and shop
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="C",
        current_balance=Decimal("4000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    txns = []
    leftovers = (Decimal("-80"), Decimal("-22.40"), Decimal("-55"), Decimal("-17.18"))
    for i, amt in enumerate(leftovers):
        t = Transaction(
            profile_id=p.id,
            account_id=cash.id,
            category_id=food.id,
            txn_date=date.today() - timedelta(days=12 * i),
            amount=amt,
            payee=f"Market {i}",
        )
        s.add(t)
        txns.append(t)
    s.commit()
    created = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": food.id,
                "period": "monthly",
                "amount": "160",
                "name": food.display_name,
            }
        ],
    )
    rid = created["changed"][0]["id"]
    small = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": shop.id,
                "period": "monthly",
                "amount": "40",
                "name": shop.display_name,
            }
        ],
    )
    small_id = small["changed"][0]["id"]
    rev = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    big = next(r for r in rev["rules"] if r["id"] == rid)
    tiny = next(r for r in rev["rules"] if r["id"] == small_id)
    assert big["over_review_floor"] is True
    assert big["activity"]["show"] is True
    assert big["activity"]["total_count"] >= 3
    assert any(t["payee"].startswith("Market") for t in big["activity"]["transactions"])
    assert tiny["activity"]["show"] is False

    moved = apply_budget_edits(
        s,
        updates=[{"id": rid, "transaction_id": txns[0].id, "category_id": shop.id}],
    )
    assert moved["changed"][0].get("reassigned") is True
    booked = apply_budget_edits(
        s,
        updates=[{"id": rid, "transaction_id": txns[1].id, "profile_id": biz.id}],
    )
    assert booked["changed"][0].get("reassigned") is True
    s.refresh(txns[1])
    assert txns[1].profile_id == biz.id
    rec = apply_budget_edits(s, updates=[{"id": rid, "recalculate": True}])
    assert rec["changed"][0].get("recalculated") is True
    rev2 = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    still = next((r for r in rev2["rules"] if r["id"] == rid), None)
    if still and still.get("activity", {}).get("transactions"):
        ids = {t["id"] for t in still["activity"]["transactions"]}
        assert txns[0].id not in ids
        assert txns[1].id not in ids
    s.close()


def test_outlier_month_does_not_become_monthly_budget(tmp_path: Path, monkeypatch):
    """A $10k one-off in utilities must not set a $10k monthly envelope."""
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    util = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.display_name.ilike("%utilit%"))
        .first()
    )
    assert util
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("8000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    for i, amt in enumerate((Decimal("-22.00"), Decimal("-19.50"), Decimal("-24.10"))):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=util.id,
                txn_date=date.today() - timedelta(days=32 * i + 3),
                amount=amt,
                payee="Payment to Prairie Power",
            )
        )
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=util.id,
                txn_date=date.today() - timedelta(days=32 * i + 10),
                amount=Decimal("-210.00") - Decimal(i),
                payee="CITY OF SPRINGFIELD WATER",
            )
        )
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            category_id=util.id,
            txn_date=date.today() - timedelta(days=40),
            amount=Decimal("-10400.00"),
            payee="CITY OF SPRINGFIELD WATER",
        )
    )
    s.add(
        Transaction(
            profile_id=p.id,
            account_id=cash.id,
            category_id=util.id,
            txn_date=date.today() - timedelta(days=15),
            amount=Decimal("-20.00"),
            payee="STATE PARKS PASS",
        )
    )
    s.commit()
    created = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": util.id,
                "period": "monthly",
                "amount": "900",
                "name": util.display_name,
            }
        ],
    )
    rid = created["changed"][0]["id"]
    rec = apply_budget_edits(s, updates=[{"id": rid, "recalculate": True}])
    out = rec["changed"][0]
    bill_names = " ".join(b["name"] for b in (out.get("bills") or [])).upper()
    assert "PRAIRIE" in bill_names or "SPRINGFIELD" in bill_names
    if not out.get("dropped"):
        assert Decimal(out["amount"]) < Decimal("500")
    s.close()


def test_stable_cell_bills_drop_budget_and_recategorize_vendor(tmp_path: Path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    p = s.query(Profile).filter(Profile.slug == "personal").one()
    cell = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.display_name.ilike("%cell%"))
        .first()
    )
    shop = (
        s.query(Category)
        .filter(Category.profile_id == p.id, Category.display_name.ilike("%shop%"))
        .first()
    )
    assert cell and shop
    cash = Account(
        profile_id=p.id,
        kind="checking",
        nickname="Ops",
        current_balance=Decimal("5000"),
        is_cash_for_ifpp=True,
    )
    s.add(cash)
    s.flush()
    for i in range(6):
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=cell.id,
                txn_date=date.today() - timedelta(days=30 * i + 8),
                amount=Decimal("-130.69"),
                payee="Payment to T-Mobile",
            )
        )
        s.add(
            Transaction(
                profile_id=p.id,
                account_id=cash.id,
                category_id=cell.id,
                txn_date=date.today() - timedelta(days=30 * i + 20),
                amount=Decimal("-99.95"),
                payee="LOVELAND PULSE",
            )
        )
    s.commit()
    created = apply_budget_edits(
        s,
        updates=[
            {
                "create": True,
                "profile_id": p.id,
                "category_id": cell.id,
                "period": "monthly",
                "amount": "195",
                "name": cell.display_name,
            }
        ],
    )
    rid = created["changed"][0]["id"]
    rec = apply_budget_edits(s, updates=[{"id": rid, "recalculate": True}])
    out = rec["changed"][0]
    assert out.get("dropped") is True
    names = {b["name"] for b in (out.get("bills") or [])}
    assert any("PULSE" in n.upper() for n in names)
    assert any("T-MOBILE" in n.upper() or "T MOBILE" in n.upper() for n in names)
    bills = s.query(ScheduledItem).filter(ScheduledItem.active.is_(True)).all()
    assert len(bills) >= 2
    highs = {abs(b.amount) for b in bills}
    assert Decimal("99.95") in highs
    assert Decimal("130.69") in highs
    rev = budgets_review(s, profile_id=p.id, seed_if_empty=False)
    assert not any(r["id"] == rid for r in rev["rules"])

    extra = Transaction(
        profile_id=p.id,
        account_id=cash.id,
        category_id=cell.id,
        txn_date=date.today(),
        amount=Decimal("-99.95"),
        payee="LOVELAND PULSE",
    )
    s.add(extra)
    s.commit()
    moved = apply_budget_edits(
        s,
        updates=[
            {
                "id": rid,
                "transaction_id": extra.id,
                "category_id": shop.id,
                "all_same_payee": True,
            }
        ],
    )
    assert moved["changed"][0].get("updated", 0) >= 2
    s.refresh(extra)
    assert extra.category_id == shop.id
    s.close()
