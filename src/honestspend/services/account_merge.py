"""Merge a mis-mapped account into another, with undo."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, AccountMerge, PromoInstallmentLine, ScheduledItem, Transaction
from honestspend.services.account_balance import apply_amount_to_account


def merge_account_into(
    session: Session,
    *,
    source_id: int,
    target_id: int,
) -> dict[str, Any]:
    if int(source_id) == int(target_id):
        raise ValueError("Source and target are the same account")
    src = session.get(Account, source_id)
    dst = session.get(Account, target_id)
    if not src or not dst:
        raise ValueError("Account not found")
    if src.archived_at is not None:
        raise ValueError("Source account is already archived")

    txns = session.query(Transaction).filter(Transaction.account_id == source_id).all()
    moved: list[int] = []
    for t in txns:
        amt = Decimal(str(t.amount or 0))
        apply_amount_to_account(src, -amt)
        t.account_id = target_id
        apply_amount_to_account(dst, amt)
        moved.append(int(t.id))

    sched_ids: list[int] = []
    for it in session.query(ScheduledItem).filter(ScheduledItem.account_id == source_id).all():
        it.account_id = target_id
        sched_ids.append(int(it.id))

    promo_ids: list[int] = []
    for ln in session.query(PromoInstallmentLine).filter(PromoInstallmentLine.account_id == source_id).all():
        ln.account_id = target_id
        promo_ids.append(int(ln.id))

    src.archived_at = datetime.now(timezone.utc)
    mid = uuid.uuid4().hex
    row = AccountMerge(
        id=mid,
        source_account_id=source_id,
        target_account_id=target_id,
        payload_json=json.dumps({"txns": moved, "schedules": sched_ids, "promos": promo_ids}),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return {
        "ok": True,
        "merge_id": mid,
        "source_account_id": source_id,
        "target_account_id": target_id,
        "moved_txn_ids": moved,
        "moved_count": len(moved),
        "source_nickname": src.nickname,
        "target_nickname": dst.nickname,
    }


def undo_account_merge(session: Session, merge_id: str) -> dict[str, Any]:
    row = session.get(AccountMerge, merge_id)
    if not row:
        raise ValueError("Merge not found")
    if row.undone_at is not None:
        raise ValueError("Merge already undone")
    src = session.get(Account, row.source_account_id)
    dst = session.get(Account, row.target_account_id)
    if not src or not dst:
        raise ValueError("Account not found")
    payload = json.loads(row.payload_json or "{}")
    for tid in payload.get("txns") or []:
        t = session.get(Transaction, int(tid))
        if t is None:
            continue
        amt = Decimal(str(t.amount or 0))
        apply_amount_to_account(dst, -amt)
        t.account_id = src.id
        apply_amount_to_account(src, amt)
    for sid in payload.get("schedules") or []:
        it = session.get(ScheduledItem, int(sid))
        if it is not None:
            it.account_id = src.id
    for pid in payload.get("promos") or []:
        ln = session.get(PromoInstallmentLine, int(pid))
        if ln is not None:
            ln.account_id = src.id
    src.archived_at = None
    row.undone_at = datetime.now(timezone.utc)
    session.flush()
    return {"ok": True, "merge_id": merge_id, "restored_account_id": src.id}
