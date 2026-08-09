"""Entity money map — list edges from transfers / intermix-style txns."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, Transaction

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def build_money_map(
    session: Session,
    *,
    days: int = 365,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max(1, days))
    profiles = {p.id: p for p in session.query(Profile).all()}
    accounts = {a.id: a for a in session.query(Account).all()}

    # Paired transfers
    pairs = (
        session.query(Transaction)
        .filter(
            Transaction.is_transfer.is_(True),
            Transaction.transfer_pair_id.isnot(None),
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.amount < 0,
            Transaction.status != "void",
        )
        .all()
    )

    edge_totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    edge_counts: dict[tuple[int, int], int] = defaultdict(int)
    samples: dict[tuple[int, int], list[str]] = defaultdict(list)

    for t in pairs:
        other = session.get(Transaction, t.transfer_pair_id)
        if not other:
            continue
        src_a = accounts.get(t.account_id)
        dst_a = accounts.get(other.account_id)
        if not src_a or not dst_a:
            continue
        key = (src_a.profile_id, dst_a.profile_id)
        if key[0] == key[1]:
            # same entity internal transfer — still record as self edge lightly
            pass
        amt = abs(_d(t.amount))
        edge_totals[key] += amt
        edge_counts[key] += 1
        if len(samples[key]) < 3:
            samples[key].append(
                f"{t.txn_date.isoformat()} {t.payee or 'transfer'} ${amt}"
            )

    edges = []
    for (src_pid, dst_pid), total in sorted(edge_totals.items(), key=lambda x: -x[1]):
        sp = profiles.get(src_pid)
        dp = profiles.get(dst_pid)
        edges.append(
            {
                "from_profile_id": src_pid,
                "from_name": sp.display_name if sp else str(src_pid),
                "from_type": sp.entity_type if sp else None,
                "to_profile_id": dst_pid,
                "to_name": dp.display_name if dp else str(dst_pid),
                "to_type": dp.entity_type if dp else None,
                "total": str(total.quantize(Decimal("0.01"))),
                "count": edge_counts[(src_pid, dst_pid)],
                "samples": samples[(src_pid, dst_pid)],
            }
        )

    nodes = [
        {
            "profile_id": p.id,
            "name": p.display_name,
            "entity_type": p.entity_type,
            "slug": p.slug,
        }
        for p in profiles.values()
        if getattr(p, "archived_at", None) is None
    ]

    return {
        "as_of": as_of.isoformat(),
        "days": days,
        "nodes": nodes,
        "edges": edges,
        "count": len(edges),
        "message": (
            f"{len(edges)} money flow edge(s) in {days}d from paired transfers."
            if edges
            else "No transfer pairs yet — use Intermix or confirm transfer matches."
        ),
    }
