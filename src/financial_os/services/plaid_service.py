"""Plaid Link + transactions sync (optional; needs FOS_PLAID_CLIENT_ID/SECRET)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from financial_os.config import settings
from financial_os.db import Account, PlaidItem, Transaction
from financial_os.services.categorizer import categorize_uncategorized


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
    from financial_os.services.secrets_store import PLAID_ITEM_TRIAL_LIMIT

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
    client_user_id: str = "financial-os-user",
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
    from financial_os.services.secrets_store import PLAID_ITEM_TRIAL_LIMIT

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
    """
    data = _post("/accounts/get", _body({"access_token": item.access_token}))
    out: list[Account] = []
    missing_current = 0
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
                from financial_os.services.cycle_config import apply_credit_cycle_defaults

                apply_credit_cycle_defaults(session, existing, source="plaid")
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
            from financial_os.services.cycle_config import apply_credit_cycle_defaults

            apply_credit_cycle_defaults(session, row, source="plaid")
        out.append(row)
    session.flush()
    return out, missing_current


def sync_transactions(session: Session, item: PlaidItem, *, auto_categorize: bool = True) -> dict[str, Any]:
    """Use /transactions/sync cursor API."""
    added = modified = removed = 0
    cursor = item.transactions_cursor or ""
    has_more = True
    # Always refresh balances from bank (Safe to spend honesty for BYOK path)
    acct_map: dict[str, Account] = {}
    synced, balances_missing_current = _sync_accounts(session, item)
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
        from financial_os.services.import_reminders import mark_import_activity

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

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "categorized": categorized,
        "institution": item.institution_name,
        "last_synced_at": item.last_synced_at.isoformat() if item.last_synced_at else None,
        # Honesty: bank omitted balances.current — books left unchanged (not zeroed)
        "balances_missing_current": balances_missing_current,
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
