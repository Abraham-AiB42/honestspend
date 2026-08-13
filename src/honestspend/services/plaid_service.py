"""Plaid Link + transactions sync (optional; needs FOS_PLAID_CLIENT_ID/SECRET)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from honestspend.config import settings
from honestspend.db import Account, PlaidItem, Transaction
from honestspend.services.categorizer import categorize_uncategorized

ZERO = Decimal("0")
CENT = Decimal("0.01")


class PlaidError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _body(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.plaid_enabled:
        raise PlaidError("Plaid not configured. Set FOS_PLAID_CLIENT_ID and FOS_PLAID_SECRET.")
    base = {
        "client_id": settings.plaid_client_id,
        "secret": settings.plaid_secret,
    }
    if extra:
        base.update(extra)
    return base


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{settings.plaid_base_url.rstrip('/')}{path}"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=_headers(), json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            msg = data.get("error_message") or data.get("display_message") or resp.text
            raise PlaidError(f"Plaid {path}: {msg}")
        return data


def item_count(session: Session) -> int:
    """Active Plaid Items (institutions). Trial free tier ≈ 10 Items."""
    return (
        session.query(PlaidItem)
        .filter(PlaidItem.status != "disconnected")
        .count()
    )


def assert_can_link_item(session: Session, *, item_id: str | None = None) -> None:
    """Enforce trial Item limit (new institution only)."""
    from honestspend.services.secrets_store import PLAID_ITEM_TRIAL_LIMIT

    if item_id:
        existing = session.query(PlaidItem).filter(PlaidItem.item_id == item_id).first()
        if existing:
            return
    n = item_count(session)
    if n >= PLAID_ITEM_TRIAL_LIMIT:
        raise PlaidError(
            f"Plaid trial limit reached ({n}/{PLAID_ITEM_TRIAL_LIMIT} institutions). "
            "Disconnect an Item in Banks (Plaid) before linking another, or upgrade on Plaid's dashboard."
        )


def create_link_token(
    session: Session | None = None,
    *,
    client_user_id: str = "honestspend-user",
) -> dict[str, Any]:
    """Create a Plaid Link token for the frontend (app-owned Link flow)."""
    if session is not None:
        assert_can_link_item(session)
    data = _post(
        "/link/token/create",
        _body(
            {
                "user": {"client_user_id": client_user_id},
                "client_name": settings.app_name,
                "products": ["transactions"],
                "country_codes": ["US"],
                "language": "en",
            }
        ),
    )
    # Surface limit info for UI
    from honestspend.services.secrets_store import PLAID_ITEM_TRIAL_LIMIT

    data = dict(data)
    if session is not None:
        data["item_count"] = item_count(session)
        data["item_limit"] = PLAID_ITEM_TRIAL_LIMIT
    return data


def exchange_public_token(
    session: Session,
    *,
    public_token: str,
    profile_id: int,
    institution_name: str | None = None,
    institution_id: str | None = None,
) -> PlaidItem:
    data = _post(
        "/item/public_token/exchange",
        _body({"public_token": public_token}),
    )
    access_token = data["access_token"]
    item_id = data["item_id"]

    existing = session.query(PlaidItem).filter(PlaidItem.item_id == item_id).first()
    if existing:
        existing.access_token = access_token
        existing.status = "active"
        item = existing
    else:
        assert_can_link_item(session, item_id=item_id)
        item = PlaidItem(
            profile_id=profile_id,
            item_id=item_id,
            access_token=access_token,
            institution_id=institution_id,
            institution_name=institution_name,
        )
        session.add(item)
    session.flush()

    _sync_accounts(session, item)
    session.flush()
    return item


def _map_account_type(acct: dict[str, Any]) -> str:
    t = (acct.get("type") or "").lower()
    st = (acct.get("subtype") or "").lower()
    if t == "credit" or st in ("credit card", "paypal"):
        return "credit"
    if st in ("savings", "money market", "cd"):
        return "savings"
    if t == "depository":
        return "checking"
    if t == "loan":
        return "loan"
    if t == "investment":
        return "investment"
    return "other"


def _sync_accounts(session: Session, item: PlaidItem) -> tuple[list[Account], int]:
    """Refresh linked accounts from /accounts/get.

    Returns (accounts, balances_missing_current) where missing count is how many
    Plaid accounts omitted balances.current (books/institution left unchanged).

    When a credit account's current_balance snapshot is applied, recomputes that
    card's payment caches / cash Card payment schedule after the pass.
    """
    data = _post("/accounts/get", _body({"access_token": item.access_token}))
    out: list[Account] = []
    missing_current = 0
    # Credit accounts whose books snapshot changed this pass → recompute schedules
    credit_balance_touched: list[int] = []
    for a in data.get("accounts") or []:
        plaid_aid = a["account_id"]
        existing = (
            session.query(Account)
            .filter(Account.plaid_account_id == plaid_aid)
            .first()
        )
        balances = a.get("balances") or {}
        current = balances.get("current")
        available = balances.get("available")
        limit = balances.get("limit")
        kind = _map_account_type(a)
        name = a.get("name") or a.get("official_name") or "Account"
        inst = item.institution_name or "Plaid"

        if existing:
            if current is not None:
                bal = Decimal(str(current))
                # Plaid balances are bank truth for linked accounts (snapshot, not txn deltas)
                existing.current_balance = bal
                existing.institution_balance = bal
                if kind in ("checking", "savings", "cash"):
                    # Promote to IFPP cash once we have a real snapshot
                    existing.is_cash_for_ifpp = True
                if kind == "credit" and existing.id is not None:
                    credit_balance_touched.append(int(existing.id))
            else:
                missing_current += 1
            if kind == "credit":
                if limit is not None:
                    existing.credit_limit = Decimal(str(limit))
                if available is not None:
                    existing.available_credit = Decimal(str(available))
                elif existing.credit_limit is not None and current is not None:
                    existing.available_credit = Decimal(str(existing.credit_limit)) - Decimal(
                        str(current)
                    )
                existing.is_cash_for_ifpp = False
                # Fill missing cycle config only; never overwrite cycle_config_source=user
                from honestspend.services.cycle_config import apply_credit_cycle_defaults

                apply_credit_cycle_defaults(
                    session, existing, source="plaid", invent_calendar=False
                )
            elif current is not None:
                existing.is_cash_for_ifpp = kind in ("checking", "savings", "cash")
            existing.plaid_item_pk = item.id
            out.append(existing)
            continue

        if current is None:
            missing_current += 1
        # Never invent $0 cash for IFPP when Plaid omits balances.current
        bal0 = Decimal(str(current)) if current is not None else Decimal("0")
        is_cash = kind in ("checking", "savings") and current is not None
        avail0: Decimal | None = None
        if kind == "credit":
            if available is not None:
                avail0 = Decimal(str(available))
            elif limit is not None and current is not None:
                # Mirror existing-account fallback so first link is honest
                avail0 = Decimal(str(limit)) - Decimal(str(current))
        row = Account(
            profile_id=item.profile_id,
            kind=kind,
            nickname=f"{inst} {name}"[:120],
            institution=inst,
            current_balance=bal0,
            institution_balance=bal0 if current is not None else None,
            credit_limit=Decimal(str(limit)) if limit is not None else None,
            available_credit=avail0,
            is_cash_for_ifpp=is_cash,
            plaid_item_pk=item.id,
            plaid_account_id=plaid_aid,
            external_id=f"plaid:{plaid_aid}",
        )
        session.add(row)
        session.flush()
        if kind == "credit":
            from honestspend.services.cycle_config import apply_credit_cycle_defaults

            apply_credit_cycle_defaults(
                session, row, source="plaid", invent_calendar=False
            )
            if current is not None and row.id is not None:
                credit_balance_touched.append(int(row.id))
        out.append(row)
    session.flush()
    if credit_balance_touched:
        from honestspend.services.autopay import after_account_balance_changed

        # De-dupe in case the same id was recorded twice
        for aid in dict.fromkeys(credit_balance_touched):
            after_account_balance_changed(session, aid)
    return out, missing_current


def _plaid_apr_rate(pct: Any) -> Decimal | None:
    """Plaid apr_percentage is display points (0, 15.24); store 0 or fraction."""
    if pct is None:
        return None
    d = Decimal(str(pct))
    if d == ZERO:
        return ZERO
    if d > 1:
        return (d / Decimal("100")).quantize(Decimal("0.0001"))
    return d.quantize(Decimal("0.0001"))


def _is_promo_apr_bucket(apr: dict[str, Any]) -> bool:
    """special bucket, or 0% with balance_subject_to_apr > 0."""
    bal_raw = apr.get("balance_subject_to_apr")
    try:
        bal = Decimal(str(bal_raw)) if bal_raw is not None else ZERO
    except Exception:
        bal = ZERO
    if bal <= ZERO:
        return False
    apr_type = (apr.get("apr_type") or "").strip().lower()
    if apr_type == "special":
        return True
    pct = apr.get("apr_percentage")
    if pct is None:
        return False
    try:
        return Decimal(str(pct)) == ZERO
    except Exception:
        return False


def apply_plaid_promo_aprs(session: Session, account_id: int, aprs: list[dict]) -> dict:
    """If any apr_type in (special,) or apr_percentage==0 with balance_subject_to_apr>0:
    upsert_promo_term kind=card_intro source=plaid. Never create purchase_plan rows."""
    from honestspend.services.promo_upsert import upsert_promo_term

    created = 0
    updated = 0
    conflicts: list[Any] = []
    lines: list[Any] = []
    as_of = date.today()

    for apr in aprs or []:
        if not isinstance(apr, dict):
            continue
        if not _is_promo_apr_bucket(apr):
            continue
        bal = Decimal(str(apr.get("balance_subject_to_apr") or 0)).quantize(CENT)
        rate = _plaid_apr_rate(apr.get("apr_percentage"))
        name = "Intro 0%" if rate is None or rate == ZERO else f"Intro {apr.get('apr_percentage')}%"
        r = upsert_promo_term(
            session,
            account_id=int(account_id),
            kind="card_intro",
            name=name,
            principal_remaining=bal,
            monthly_payment=ZERO,
            start_date=as_of,
            end_date=None,
            source="plaid",
            apr=rate if rate is not None else ZERO,
        )
        lines.append(r.get("line"))
        if r.get("created"):
            created += 1
        else:
            updated += 1
        if r.get("conflict"):
            conflicts.append(r["conflict"])

    session.flush()
    return {
        "created": created,
        "updated": updated,
        "conflicts": conflicts,
        "lines": lines,
        "promos_found": created + updated,
        "promo_conflicts": len(conflicts),
    }


def _sync_liabilities(session: Session, item: PlaidItem) -> dict[str, Any]:
    """Best-effort /liabilities/get — special/0% APR buckets → card_intro.

    Soft-fails when Liabilities product is not on the Item (transactions-only).
    Never invents purchase_plan rows from Plaid.
    """
    try:
        data = _post("/liabilities/get", _body({"access_token": item.access_token}))
    except Exception:
        return {"ok": False, "created": 0, "updated": 0, "conflicts": 0}

    by_plaid: dict[str, Account] = {
        a.plaid_account_id: a
        for a in session.query(Account).filter(Account.plaid_item_pk == item.id).all()
        if a.plaid_account_id
    }
    created = updated = conflict_n = 0
    liabilities = data.get("liabilities") or {}
    for credit in liabilities.get("credit") or []:
        if not isinstance(credit, dict):
            continue
        plaid_aid = credit.get("account_id")
        acct = by_plaid.get(plaid_aid) if plaid_aid else None
        if acct is None or acct.id is None:
            continue
        # Refresh min payment when present and account empty
        min_pay = credit.get("minimum_payment_amount")
        if min_pay is not None and getattr(acct, "min_payment", None) in (None, ZERO):
            try:
                acct.min_payment = Decimal(str(min_pay))
            except Exception:
                pass
        due = credit.get("next_payment_due_date")
        if due and not getattr(acct, "payment_due_day", None):
            try:
                if isinstance(due, str) and len(due) >= 10:
                    acct.payment_due_day = int(due[8:10])
                    if not getattr(acct, "cycle_config_source", None):
                        acct.cycle_config_source = "plaid"
            except Exception:
                pass
        r = apply_plaid_promo_aprs(session, int(acct.id), credit.get("aprs") or [])
        created += int(r.get("created") or 0)
        updated += int(r.get("updated") or 0)
        conflict_n += int(r.get("promo_conflicts") or len(r.get("conflicts") or []))
    session.flush()
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "conflicts": conflict_n,
    }


def sync_transactions(session: Session, item: PlaidItem, *, auto_categorize: bool = True) -> dict[str, Any]:
    """Use /transactions/sync cursor API."""
    added = modified = removed = 0
    cursor = item.transactions_cursor or ""
    has_more = True
    # Always refresh balances from bank (Safe to spend honesty for BYOK path)
    acct_map: dict[str, Account] = {}
    synced, balances_missing_current = _sync_accounts(session, item)
    # Best-effort liabilities (APR / special 0% → card_intro)
    liabilities_promo = _sync_liabilities(session, item)
    for a in synced:
        if a.plaid_account_id:
            acct_map[a.plaid_account_id] = a
    if not acct_map:
        # Fallback if accounts/get returned nothing
        acct_map = {
            a.plaid_account_id: a
            for a in session.query(Account).filter(Account.plaid_item_pk == item.id).all()
            if a.plaid_account_id
        }

    while has_more:
        data = _post(
            "/transactions/sync",
            _body(
                {
                    "access_token": item.access_token,
                    "cursor": cursor,
                    "count": 500,
                }
            ),
        )
        for t in data.get("added") or []:
            if _upsert_plaid_txn(session, item, acct_map, t):
                added += 1
        for t in data.get("modified") or []:
            if _upsert_plaid_txn(session, item, acct_map, t):
                modified += 1
        for t in data.get("removed") or []:
            tid = t.get("transaction_id")
            if not tid:
                continue
            row = (
                session.query(Transaction)
                .filter(Transaction.external_id == f"plaid:{tid}")
                .first()
            )
            if row:
                session.delete(row)
                removed += 1
        has_more = bool(data.get("has_more"))
        cursor = data.get("next_cursor") or cursor

    item.transactions_cursor = cursor
    item.last_synced_at = datetime.now()
    session.flush()
    try:
        from honestspend.services.import_reminders import mark_import_activity

        mark_import_activity(session, when=item.last_synced_at)
    except Exception:
        pass

    categorized = 0
    if auto_categorize and added:
        results = categorize_uncategorized(
            session,
            profile_id=item.profile_id,
            limit=min(500, added + 50),
            apply=True,
            use_grok=False,
            min_confidence=0.85,
        )
        categorized = sum(1 for r in results if r.get("applied"))

    # Honesty: bank already paid the bill — advance matching schedules (high confidence only)
    schedules_advanced = 0
    schedules_advanced_names: list[str] = []
    schedule_advance_hint: str | None = None
    schedule_advance_error: str | None = None
    if added > 0:
        try:
            from honestspend.services.schedule_mark_paid import advance_schedules_after_import

            # Profile-wide: item may span multiple cash accounts; matcher uses each schedule's account_id
            adv = advance_schedules_after_import(
                session,
                account_id=None,
                profile_id=item.profile_id,
                auto_apply=True,
            )
            schedules_advanced = int(adv.get("advanced_count") or 0)
            schedules_advanced_names = [
                str(x.get("name") or "") for x in (adv.get("advanced") or []) if x.get("name")
            ]
            schedule_advance_hint = adv.get("hint")
        except Exception as e:
            # Sync still succeeded; surface advance failure without failing the sync
            schedules_advanced = 0
            schedule_advance_error = str(e)[:300]

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "categorized": categorized,
        "institution": item.institution_name,
        "last_synced_at": item.last_synced_at.isoformat() if item.last_synced_at else None,
        # Honesty: bank omitted balances.current — books left unchanged (not zeroed)
        "balances_missing_current": balances_missing_current,
        "schedules_advanced": schedules_advanced,
        "schedules_advanced_names": schedules_advanced_names,
        "schedule_advance_hint": schedule_advance_hint,
        "schedule_advance_error": schedule_advance_error,
        "liabilities_promo": liabilities_promo,
    }


def _upsert_plaid_txn(
    session: Session,
    item: PlaidItem,
    acct_map: dict[str, Account],
    t: dict[str, Any],
) -> bool:
    tid = t.get("transaction_id")
    aid = t.get("account_id")
    if not tid or not aid:
        return False
    acct = acct_map.get(aid)
    if not acct:
        return False

    # Plaid: positive amount = money leaving the account (outflow). Ledger: expenses negative.
    # Snapshot balances come from /accounts/get — do not re-normalize credit signs here.
    raw_amt = Decimal(str(t.get("amount") or 0))
    amount = -raw_amt

    external_id = f"plaid:{tid}"
    existing = (
        session.query(Transaction).filter(Transaction.external_id == external_id).first()
    )
    payee = t.get("merchant_name") or t.get("name") or "Plaid"
    txn_date = t.get("date") or t.get("authorized_date")
    if isinstance(txn_date, str):
        from datetime import date as date_cls

        d = date_cls.fromisoformat(txn_date[:10])
    else:
        return False

    pending = bool(t.get("pending"))
    status = "pending" if pending else "cleared"

    if existing:
        existing.amount = amount
        existing.payee = payee
        existing.txn_date = d
        existing.status = status
        # Balance comes from /accounts/get snapshot — do not double-apply deltas
        return True

    session.add(
        Transaction(
            profile_id=item.profile_id,
            account_id=acct.id,
            txn_date=d,
            amount=amount,
            payee=payee,
            memo=t.get("original_description") or "Plaid",
            status=status,
            external_id=external_id,
        )
    )
    # Ledger history only; Safe to spend uses institution snapshot from _sync_accounts
    return True


def list_items(session: Session) -> list[dict[str, Any]]:
    rows = session.query(PlaidItem).order_by(PlaidItem.id.desc()).all()
    out = []
    for r in rows:
        age_hours = None
        if r.last_synced_at:
            delta = datetime.now() - r.last_synced_at
            age_hours = round(delta.total_seconds() / 3600.0, 1)
        out.append(
            {
                "id": r.id,
                "profile_id": r.profile_id,
                "item_id": r.item_id,
                "institution_name": r.institution_name,
                "status": r.status,
                "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
                "sync_age_hours": age_hours,
                "needs_reauth": (r.status or "").lower() in ("login_required", "error", "pending_expiration"),
                "accounts": session.query(Account).filter(Account.plaid_item_pk == r.id).count(),
            }
        )
    return out


def disconnect_item(session: Session, item_pk: int, *, keep_accounts: bool = True) -> dict[str, Any]:
    """Mark Plaid item disconnected and clear access token. Optionally keep local accounts."""
    item = session.get(PlaidItem, item_pk)
    if not item:
        raise ValueError("Plaid item not found")
    item.status = "disconnected"
    item.access_token = ""
    item.transactions_cursor = None
    accts = session.query(Account).filter(Account.plaid_item_pk == item.id).all()
    for a in accts:
        if keep_accounts:
            a.plaid_item_pk = None
            a.plaid_account_id = None
        else:
            a.archived_at = datetime.now()
            a.plaid_item_pk = None
            a.plaid_account_id = None
    session.flush()
    return {
        "ok": True,
        "id": item.id,
        "status": item.status,
        "accounts_unlinked": len(accts),
        "keep_accounts": keep_accounts,
    }


def sandbox_public_token() -> str:
    """Create a sandbox public token for testing without Link UI."""
    data = _post(
        "/sandbox/public_token/create",
        _body(
            {
                "institution_id": "ins_109508",
                "initial_products": ["transactions"],
            }
        ),
    )
    return data["public_token"]
