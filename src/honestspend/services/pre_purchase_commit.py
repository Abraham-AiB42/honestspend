"""I charged it — write charge (+ optional purchase-plan line) after Can I buy? check."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from honestspend.db import Account, PromoInstallmentLine, Transaction
from honestspend.services.promo_installments import create_promo_line, line_to_dict
from honestspend.services.smart_charge import rank_charge

ZERO = Decimal("0")
CENT = Decimal("0.01")


class UnsafePurchase(Exception):
    """Raised when post account is unsafe and confirm_unsafe is false."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("message", "unsafe_purchase"))
        self.payload = payload


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return _d(v).quantize(CENT)


def _promo_mode(promo: dict | None) -> str:
    if not promo:
        return "none"
    return str(promo.get("mode") or "none").strip().lower() or "none"


def _purchase_plan_monthly(promo: dict | None, amount: Decimal) -> Decimal:
    if _promo_mode(promo) != "purchase_plan" or not promo:
        return ZERO
    raw = promo.get("monthly")
    if raw not in (None, ""):
        return _q(_d(raw))
    months = promo.get("months")
    if months in (None, ""):
        return ZERO
    n = int(months)
    if n <= 0:
        return ZERO
    return (amount / Decimal(n)).quantize(CENT, rounding=ROUND_HALF_UP)


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def _commit_tag(key: str) -> str:
    return f"commit:{key.strip()}"


def _find_by_commit_key(session: Session, commit_key: str) -> Transaction | None:
    tag = _commit_tag(commit_key)
    return (
        session.query(Transaction)
        .filter(
            or_(
                Transaction.external_id == tag,
                Transaction.memo == tag,
                Transaction.memo.like(f"%{tag}%"),
            )
        )
        .order_by(Transaction.id.asc())
        .first()
    )


def _txn_to_dict(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "profile_id": txn.profile_id,
        "account_id": txn.account_id,
        "category_id": txn.category_id,
        "txn_date": txn.txn_date.isoformat() if txn.txn_date else None,
        "amount": str(_q(_d(txn.amount))),
        "payee": txn.payee,
        "memo": txn.memo,
        "status": txn.status or "cleared",
        "is_transfer": bool(txn.is_transfer),
        "external_id": txn.external_id,
    }


def _option_dict(opt: dict | None) -> dict | None:
    if not opt:
        return None
    out = dict(opt)
    for k in ("rewards_rate", "remaining_spendable", "next_payment_after"):
        if k in out and out[k] is not None and not isinstance(out[k], str):
            out[k] = str(out[k])
    return out


def _existing_result(
    session: Session,
    txn: Transaction,
    *,
    ranked: dict | None = None,
) -> dict[str, Any]:
    line = (
        session.query(PromoInstallmentLine)
        .filter(PromoInstallmentLine.linked_txn_id == txn.id)
        .first()
    )
    posted_acct = session.get(Account, txn.account_id)
    posted = {
        "account_id": txn.account_id,
        "account_name": posted_acct.nickname if posted_acct else None,
        "method": (
            "card"
            if posted_acct and (posted_acct.kind or "").lower() == "credit"
            else "cash"
        ),
        "amount": str(_q(_d(txn.amount))),
    }
    return {
        "transaction": _txn_to_dict(txn),
        "promo_line": line_to_dict(line) if line else None,
        "recommended": _option_dict((ranked or {}).get("recommended")),
        "posted": posted,
        "idempotent": True,
    }


def commit_purchase(
    session: Session,
    *,
    amount: Decimal,
    post_account_id: int,
    proposed_account_id: int,
    recommended_account_id: int,
    reward_category: str = "general",
    promo: dict | None = None,
    category_id: int | None = None,
    payee: str | None = None,
    confirm_unsafe: bool = False,
    commit_key: str | None = None,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """post_account_id must be recommended or proposed.

    Re-run rank_charge; if post is unsafe and not confirm_unsafe → raise UnsafePurchase.
    Write Transaction (−amount). If promo.mode==purchase_plan: create_promo_line.
    recompute_card_payment_schedule if credit.
    Idempotent on commit_key via external_id/memo `commit:{key}`.
    Returns {transaction, promo_line, recommended, posted}.
    """
    as_of = as_of or date.today()
    amount = abs(_d(amount))
    if amount <= ZERO:
        raise ValueError("amount must be positive")

    key = (commit_key or "").strip() or None
    if key:
        existing = _find_by_commit_key(session, key)
        if existing is not None:
            ranked = rank_charge(
                session,
                amount=amount,
                reward_category=reward_category or "general",
                proposed_account_id=int(proposed_account_id),
                as_of=as_of,
                profile_id=profile_id,
                promo=promo,
                budget_category_id=category_id,
            )
            return _existing_result(session, existing, ranked=ranked)

    allowed = {int(proposed_account_id), int(recommended_account_id)}
    if int(post_account_id) not in allowed:
        raise ValueError(
            "post_account_id must be the recommended or proposed account from the check"
        )

    post_acct = session.get(Account, int(post_account_id))
    if post_acct is None:
        raise ValueError(f"Account {post_account_id} not found")

    ranked = rank_charge(
        session,
        amount=amount,
        reward_category=reward_category or "general",
        proposed_account_id=int(proposed_account_id),
        as_of=as_of,
        profile_id=profile_id,
        promo=promo,
        budget_category_id=category_id,
    )

    post_opt = next(
        (
            o
            for o in (ranked.get("options") or [])
            if o.get("account_id") is not None
            and int(o["account_id"]) == int(post_account_id)
        ),
        None,
    )
    if post_opt is None:
        # Proposed may be missing from options if archived; still refuse unsafe
        post_opt = {
            "account_id": post_acct.id,
            "account_name": post_acct.nickname,
            "method": (
                "card" if (post_acct.kind or "").lower() == "credit" else "cash"
            ),
            "safe": False,
            "reason": "Account is not a ranked charge candidate.",
        }

    if not post_opt.get("safe") and not confirm_unsafe:
        raise UnsafePurchase(
            {
                "code": "unsafe_purchase",
                "message": (
                    post_opt.get("reason")
                    or f"Posting to {post_acct.nickname} is not safe for this amount."
                ),
                "account_id": post_acct.id,
                "account_name": post_acct.nickname,
                "reason": post_opt.get("reason"),
                "confirm_required": True,
                "recommended": _option_dict(ranked.get("recommended")),
                "posted_option": _option_dict(post_opt),
            }
        )

    pid = profile_id if profile_id is not None else post_acct.profile_id
    signed = -_q(amount)
    memo = None
    external_id = None
    if key:
        tag = _commit_tag(key)
        external_id = tag
        memo = tag

    from honestspend.services.account_balance import apply_amount_to_account
    from honestspend.services.never_neg import WouldGoNegative, check_cash_outflow

    try:
        check_cash_outflow(
            session,
            account=post_acct,
            amount=signed,
            confirm_unsafe=confirm_unsafe,
        )
    except WouldGoNegative:
        raise

    txn = Transaction(
        profile_id=int(pid),
        account_id=post_acct.id,
        category_id=category_id,
        txn_date=as_of,
        amount=signed,
        payee=payee,
        memo=memo,
        status="cleared",
        is_transfer=False,
        external_id=external_id,
    )
    session.add(txn)
    session.flush()

    # Balance first without recompute so purchase-plan lines can land before schedule.
    apply_amount_to_account(
        post_acct,
        signed,
        confirm_unsafe=confirm_unsafe,
        session=session,
        recompute=False,
    )

    promo_line = None
    if _promo_mode(promo) == "purchase_plan" and (post_acct.kind or "").lower() == "credit":
        monthly = _purchase_plan_monthly(promo, amount)
        months_raw = (promo or {}).get("months")
        end: date | None = None
        if months_raw not in (None, ""):
            n = int(months_raw)
            if n > 0:
                end = _add_months(as_of, n)
        name = (payee or "").strip() or "Purchase plan"
        if payee:
            name = f"Purchase plan · {payee.strip()}"
        promo_line = create_promo_line(
            session,
            post_acct.id,
            name=name,
            principal_remaining=amount,
            monthly_payment=monthly,
            start_date=as_of,
            end_date=end,
            source="user",
            kind="purchase_plan",
            status="open",
            linked_txn_id=txn.id,
        )

    if (post_acct.kind or "").lower() == "credit":
        from honestspend.services.autopay import recompute_card_payment_schedule

        recompute_card_payment_schedule(session, post_acct.id, as_of=as_of)

    session.flush()
    session.refresh(txn)

    posted = {
        "account_id": post_acct.id,
        "account_name": post_acct.nickname,
        "method": "card" if (post_acct.kind or "").lower() == "credit" else "cash",
        "amount": str(signed),
        "safe": bool(post_opt.get("safe")),
        "reason": post_opt.get("reason"),
    }
    return {
        "transaction": _txn_to_dict(txn),
        "promo_line": line_to_dict(promo_line) if promo_line else None,
        "recommended": _option_dict(ranked.get("recommended")),
        "posted": posted,
    }
