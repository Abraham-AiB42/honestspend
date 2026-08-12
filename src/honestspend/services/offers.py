"""Offer desk: pending mailers (new card / BT / plan) + take / skip / plan."""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile, PromoInstallmentLine, Transaction
from honestspend.services.promo_installments import create_promo_line, line_to_dict
from honestspend.services.smart_charge import rank_charge

ZERO = Decimal("0")
CENT = Decimal("0.01")
OFFER_TYPES = frozenset({"new_card", "balance_transfer", "purchase_plan"})
DECISIONS = frozenset({"take", "take_with_plan", "skip"})
BOOKS_KILLS_PROMO = frozenset({"books"})
_TYPE_ALIASES = {
    "bt": "balance_transfer",
    "balance-transfer": "balance_transfer",
    "card": "new_card",
    "new-card": "new_card",
    "plan": "purchase_plan",
    "purchase-plan": "purchase_plan",
}


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal | Any) -> Decimal:
    return _d(v).quantize(CENT)


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def _norm_offer_type(raw: str) -> str:
    s = (raw or "").strip().lower().replace(" ", "_")
    s = _TYPE_ALIASES.get(s, s)
    if s not in OFFER_TYPES:
        raise ValueError(
            f"Unknown offer_type {raw!r}; use new_card | balance_transfer | purchase_plan"
        )
    return s


def _first(terms: dict, *keys, default=None):
    for k in keys:
        if k in terms and terms[k] not in (None, ""):
            return terms[k]
    return default


def _as_int(v: Any) -> int | None:
    if v in (None, ""):
        return None
    return int(v)


def _as_date(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date) and not isinstance(v, type(date.min)):
        return v
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _apr_rate(apr: Any) -> Decimal:
    """Account APR is stored as a decimal (0.24); values >= 1 are percent points."""
    a = _d(apr)
    if a <= ZERO:
        return ZERO
    if a >= Decimal("1"):
        return a / Decimal("100")
    return a


def _fee_amount(amount: Decimal, terms: dict) -> Decimal:
    raw_fee = _first(terms, "fee", "fee_amount")
    if raw_fee not in (None, ""):
        return _q(raw_fee)
    raw_pct = _first(terms, "fee_pct", "fee_percent", "fee_rate")
    if raw_pct in (None, ""):
        return ZERO
    pct = _d(raw_pct)
    if pct > Decimal("1"):
        pct = pct / Decimal("100")
    return (amount * pct).quantize(CENT, rounding=ROUND_HALF_UP)


def _interest_saved(amount: Decimal, apr: Any, months: int) -> Decimal:
    if amount <= ZERO or months <= 0:
        return ZERO
    return (amount * _apr_rate(apr) * Decimal(months) / Decimal("12")).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def _encode_terms(payload: dict[str, Any]) -> str:
    clean = {k: v for k, v in payload.items() if v not in (None, "", [])}
    raw = json.dumps(clean, separators=(",", ":"), default=str)
    if len(raw) <= 256:
        return raw
    compact = {
        "t": clean.get("offer_type"),
        "f": clean.get("from_account_id"),
        "d": clean.get("destination_account_id"),
        "a": clean.get("amount"),
        "e": clean.get("fee"),
        "p": clean.get("fee_pct"),
        "m": clean.get("months"),
        "l": clean.get("credit_limit"),
        "c": clean.get("close_day"),
        "u": clean.get("due_day"),
        "i": clean.get("profile_id"),
    }
    raw = json.dumps(
        {k: v for k, v in compact.items() if v not in (None, "")},
        separators=(",", ":"),
        default=str,
    )
    return raw[:256]


def _decode_fingerprint(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).lstrip().startswith("{"):
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    aliases = {
        "t": "offer_type",
        "f": "from_account_id",
        "d": "destination_account_id",
        "a": "amount",
        "e": "fee",
        "p": "fee_pct",
        "m": "months",
        "l": "credit_limit",
        "c": "close_day",
        "u": "due_day",
        "i": "profile_id",
    }
    out = dict(data)
    for short, long in aliases.items():
        if short in out and long not in out:
            out[long] = out[short]
    return out


def terms_from_offer(offer: PromoInstallmentLine) -> dict[str, Any]:
    extra = _decode_fingerprint(getattr(offer, "fingerprint", None))
    amount = extra.get("amount", offer.principal_remaining)
    monthly = extra.get("monthly", extra.get("monthly_payment", offer.monthly_payment))
    dest = extra.get("destination_account_id") or extra.get("dest_account_id")
    if dest is None and (offer.offer_type or "") != "new_card":
        dest = offer.account_id
    months = extra.get("months")
    start = offer.start_date
    end = offer.end_date
    if months in (None, "") and start and end:
        months = (end.year - start.year) * 12 + (end.month - start.month)
    return {
        "offer_type": offer.offer_type or extra.get("offer_type"),
        "name": offer.name,
        "account_id": offer.account_id,
        "from_account_id": extra.get("from_account_id") or extra.get("source_account_id"),
        "destination_account_id": dest,
        "amount": _q(amount) if amount not in (None, "") else ZERO,
        "monthly": _q(monthly) if monthly not in (None, "") else ZERO,
        "months": int(months) if months not in (None, "") else 0,
        "fee": extra.get("fee"),
        "fee_pct": extra.get("fee_pct") or extra.get("fee_percent"),
        "apr": extra.get("apr", extra.get("intro_apr", offer.apr)),
        "intro_apr": extra.get("intro_apr", extra.get("apr", offer.apr)),
        "start_date": start,
        "end_date": end,
        "credit_limit": extra.get("credit_limit") or extra.get("limit"),
        "close_day": extra.get("close_day") or extra.get("statement_close_day"),
        "due_day": extra.get("due_day") or extra.get("payment_due_day"),
        "annual_fee": extra.get("annual_fee"),
        "rewards_program": extra.get("rewards_program") or extra.get("rewards"),
        "profile_id": extra.get("profile_id"),
        "autopay_policy": extra.get("autopay_policy"),
        "nickname": extra.get("nickname") or offer.name,
    }


def offer_to_dict(offer: PromoInstallmentLine) -> dict[str, Any]:
    out = line_to_dict(offer)
    out["terms"] = terms_from_offer(offer)
    return out


def list_offers(
    session: Session,
    *,
    status: str | None = "pending",
    include_verdict: bool = True,
) -> list[dict[str, Any]]:
    q = session.query(PromoInstallmentLine).filter(PromoInstallmentLine.kind == "offer")
    if status:
        q = q.filter(PromoInstallmentLine.status == status)
    rows = q.order_by(PromoInstallmentLine.id).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = offer_to_dict(row)
        if include_verdict and (row.status or "") == "pending":
            item["verdict"] = offer_verdict(session, row)
        items.append(item)
    return items


def create_offer(session: Session, *, offer_type: str, **terms) -> PromoInstallmentLine:
    """kind=offer, status=pending. new_card may have account_id=None."""
    ot = _norm_offer_type(offer_type)
    start = _as_date(_first(terms, "start_date", "as_of")) or date.today()
    months_raw = _first(terms, "months")
    months = int(months_raw) if months_raw not in (None, "") else 0
    end = _as_date(_first(terms, "end_date"))
    if end is None and months > 0:
        end = _add_months(start, months)

    amount = _d(_first(terms, "amount", "principal", "principal_remaining", default=0))
    monthly = _d(_first(terms, "monthly", "monthly_payment", default=0))
    if monthly <= ZERO and months > 0 and amount > ZERO and ot == "purchase_plan":
        monthly = (amount / Decimal(months)).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount <= ZERO and monthly > ZERO and months > 0:
        amount = _q(monthly * Decimal(months))

    dest_id = _as_int(
        _first(
            terms,
            "destination_account_id",
            "dest_account_id",
            "to_account_id",
            "account_id",
        )
    )
    from_id = _as_int(_first(terms, "from_account_id", "source_account_id"))
    if ot == "new_card":
        account_id = None
    elif ot == "balance_transfer":
        account_id = dest_id
    else:
        account_id = dest_id

    intro_apr = _first(terms, "intro_apr", "apr")
    apr_d = _d(intro_apr) if intro_apr not in (None, "") else (
        ZERO if ot in ("new_card", "balance_transfer") else None
    )
    name = (
        str(_first(terms, "name", "nickname") or "").strip()
        or ("New card offer" if ot == "new_card" else "Promo offer")
    )
    fee = _fee_amount(amount, terms)
    fee_pct = _first(terms, "fee_pct", "fee_percent", "fee_rate")
    payload = {
        "offer_type": ot,
        "from_account_id": from_id,
        "destination_account_id": dest_id,
        "amount": str(_q(amount)) if amount else None,
        "monthly": str(_q(monthly)) if monthly else None,
        "fee": str(fee) if fee else None,
        "fee_pct": str(fee_pct) if fee_pct not in (None, "") else None,
        "months": months or None,
        "credit_limit": str(_first(terms, "credit_limit", "limit") or "") or None,
        "close_day": _as_int(_first(terms, "close_day", "statement_close_day")),
        "due_day": _as_int(_first(terms, "due_day", "payment_due_day")),
        "annual_fee": str(_first(terms, "annual_fee") or "") or None,
        "rewards_program": _first(terms, "rewards_program", "rewards"),
        "profile_id": _as_int(_first(terms, "profile_id")),
        "autopay_policy": _first(terms, "autopay_policy"),
        "nickname": _first(terms, "nickname"),
        "intro_apr": str(apr_d) if apr_d is not None else None,
    }
    line = create_promo_line(
        session,
        account_id,
        name=name,
        principal_remaining=amount if ot != "new_card" else ZERO,
        monthly_payment=monthly,
        start_date=start,
        end_date=end,
        source=str(_first(terms, "source") or "user"),
        kind="offer",
        status="pending",
        offer_type=ot,
        fingerprint=_encode_terms(payload),
        apr=apr_d,
        active=True,
    )
    return line


def _policy_kills_zero(acct: Account | None) -> bool:
    if acct is None:
        return False
    pol = (acct.autopay_policy or "").strip().lower()
    return pol in BOOKS_KILLS_PROMO


def _safe_policy(acct: Account | None) -> str:
    if acct is not None and (acct.autopay_policy or "").strip().lower() == "promo_sink":
        return "promo_sink"
    return "statement"


def _profile_id(session: Session, terms: dict, *accts: Account | None) -> int:
    raw = _as_int(terms.get("profile_id"))
    if raw:
        return raw
    for a in accts:
        if a is not None:
            return int(a.profile_id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    if p:
        return int(p.id)
    first = session.query(Profile).order_by(Profile.id).first()
    if not first:
        raise ValueError("No profile available for this offer")
    return int(first.id)


def _ranker_safe(
    session: Session,
    *,
    amount: Decimal,
    proposed_id: int | None,
    as_of: date,
    profile_id: int | None,
    promo: dict | None,
) -> tuple[bool, dict]:
    if not proposed_id or amount <= ZERO:
        return True, {}
    try:
        ranked = rank_charge(
            session,
            amount=amount,
            reward_category="general",
            proposed_account_id=int(proposed_id),
            as_of=as_of,
            profile_id=profile_id,
            promo=promo,
        )
    except Exception:
        return False, {}
    proposed = ranked.get("proposed") or {}
    return bool(proposed.get("safe")), ranked


def offer_verdict(session: Session, offer: PromoInstallmentLine) -> dict:
    """take | take_with_plan | skip + why."""
    terms = terms_from_offer(offer)
    ot = _norm_offer_type(str(terms.get("offer_type") or offer.offer_type or "new_card"))
    amount = _q(terms.get("amount") or 0)
    months = int(terms.get("months") or 0)
    as_of = terms.get("start_date") or offer.start_date or date.today()
    dest_id = _as_int(terms.get("destination_account_id") or terms.get("account_id"))
    from_id = _as_int(terms.get("from_account_id"))
    dest = session.get(Account, dest_id) if dest_id else None
    src = session.get(Account, from_id) if from_id else None
    fee = _fee_amount(amount, terms)
    src_apr = None
    if src is not None and src.apr is not None:
        src_apr = src.apr
    elif dest is not None and dest.apr is not None and ot != "balance_transfer":
        src_apr = dest.apr
    interest = _interest_saved(amount, src_apr, months)
    pid = _profile_id(session, terms, dest, src)

    why = ""
    verdict = "take"
    safe = True
    ranked: dict = {}

    if ot == "balance_transfer":
        if fee > interest:
            verdict = "skip"
            why = (
                f"Fee ${fee} is more than about ${interest} of interest you'd otherwise pay."
            )
            safe = False
        else:
            promo = {"mode": "card_intro"}
            proposed_id = dest.id if dest is not None else from_id
            safe, ranked = _ranker_safe(
                session,
                amount=amount + fee if amount + fee > ZERO else Decimal("1.00"),
                proposed_id=proposed_id,
                as_of=as_of,
                profile_id=pid,
                promo=promo,
            )
            if not safe:
                verdict = "skip"
                why = (
                    ranked.get("why")
                    or "This transfer would squeeze Safe to spend or has no proven pay date."
                )
            elif _policy_kills_zero(dest):
                verdict = "take_with_plan"
                why = (
                    "Take it with a plan — paying books/current balance would kill the 0%. "
                    "Use statement or a promo sink."
                )
            else:
                verdict = "take"
                why = (
                    f"Fee ${fee} is less than about ${interest} of interest saved, "
                    "and next payment still fits Safe."
                )
    elif ot == "purchase_plan":
        monthly = _q(terms.get("monthly") or 0)
        promo = {"mode": "purchase_plan", "months": months or None, "monthly": str(monthly)}
        card = dest or src
        proposed_id = card.id if card is not None else None
        check_amt = amount if amount > ZERO else Decimal("1.00")
        safe, ranked = _ranker_safe(
            session,
            amount=check_amt,
            proposed_id=proposed_id,
            as_of=as_of,
            profile_id=pid,
            promo=promo,
        )
        if not safe:
            verdict = "skip"
            why = (
                ranked.get("why")
                or "This plan's monthly would squeeze Safe to spend."
            )
        elif _policy_kills_zero(card):
            verdict = "take_with_plan"
            why = (
                "Take it with a plan — books/pay-current would treat the full amount as due "
                "and kill the 0% window."
            )
        else:
            verdict = "take"
            why = "Purchase plan monthly still fits Safe to spend."
    else:
        # new_card — useful 0% window is a take; books isn't on the card yet
        verdict = "take"
        why = "Open the card and park an intro 0% window. No charge until you use it."
        if months <= 0 and _d(terms.get("intro_apr") or terms.get("apr") or 0) > ZERO:
            annual = _d(terms.get("annual_fee") or 0)
            if annual > ZERO:
                verdict = "skip"
                why = "No 0% intro and an annual fee — an existing card already wins."

    return {
        "verdict": verdict,
        "why": why,
        "fee": str(_q(fee)),
        "interest_saved": str(_q(interest)),
        "safe": safe,
        "ranker": {
            "verdict": ranked.get("verdict"),
            "why": ranked.get("why"),
            "safe_to_spend": ranked.get("safe_to_spend"),
        }
        if ranked
        else None,
    }


def _acct_dict(acct: Account | None) -> dict[str, Any] | None:
    if acct is None:
        return None
    return {
        "id": acct.id,
        "nickname": acct.nickname,
        "kind": acct.kind,
        "current_balance": str(_q(acct.current_balance)),
        "credit_limit": str(_q(acct.credit_limit)) if acct.credit_limit is not None else None,
        "autopay_policy": acct.autopay_policy,
        "promo_apr": str(acct.promo_apr) if acct.promo_apr is not None else None,
        "promo_end_date": acct.promo_end_date.isoformat() if acct.promo_end_date else None,
    }


def _txn_dict(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "amount": str(_q(txn.amount)),
        "payee": txn.payee,
        "memo": txn.memo,
        "is_transfer": bool(txn.is_transfer),
        "txn_date": txn.txn_date.isoformat() if txn.txn_date else None,
    }


def _create_credit_account(
    session: Session,
    *,
    terms: dict,
    name: str,
    as_of: date,
    policy: str | None = None,
) -> Account:
    from honestspend.services.cycle_config import apply_credit_cycle_defaults

    pid = _profile_id(session, terms)
    limit_raw = terms.get("credit_limit") or terms.get("limit")
    limit = _q(limit_raw) if limit_raw not in (None, "") else None
    close_day = _as_int(terms.get("close_day") or terms.get("statement_close_day"))
    due_day = _as_int(terms.get("due_day") or terms.get("payment_due_day"))
    intro_apr = terms.get("intro_apr")
    if intro_apr in (None, ""):
        intro_apr = terms.get("apr")
    apr_d = _d(intro_apr) if intro_apr not in (None, "") else ZERO
    end = terms.get("end_date")
    months = int(terms.get("months") or 0)
    if end is None and months > 0:
        end = _add_months(as_of, months)
    pol = (policy or terms.get("autopay_policy") or "statement").strip().lower()
    acct = Account(
        profile_id=pid,
        kind="credit",
        nickname=name,
        current_balance=ZERO,
        credit_limit=limit,
        available_credit=limit,
        statement_close_day=close_day,
        payment_due_day=due_day,
        promo_apr=apr_d,
        promo_end_date=end,
        promo_balance=ZERO,
        rewards_program=(
            str(terms.get("rewards_program")).strip()
            if terms.get("rewards_program")
            else None
        ),
        autopay_policy=pol,
        min_payment=Decimal("25"),
    )
    session.add(acct)
    session.flush()
    apply_credit_cycle_defaults(
        session,
        acct,
        source="default",
        statement_close_day=close_day,
        payment_due_day=due_day,
        autopay_policy=pol,
    )
    return acct


def _open_intro(
    session: Session,
    acct: Account,
    *,
    name: str,
    principal: Decimal,
    as_of: date,
    end: date | None,
    apr: Decimal | None,
) -> PromoInstallmentLine:
    return create_promo_line(
        session,
        acct.id,
        name=name,
        principal_remaining=_q(principal),
        monthly_payment=ZERO,
        start_date=as_of,
        end_date=end,
        source="user",
        kind="card_intro",
        status="open",
        apr=_d(apr) if apr is not None else ZERO,
        active=True,
    )


def _apply_policy_if_needed(acct: Account | None, *, decision: str) -> None:
    if acct is None or decision != "take_with_plan":
        return
    if _policy_kills_zero(acct):
        acct.autopay_policy = _safe_policy(acct)


def _recompute(session: Session, account_id: int | None, as_of: date) -> None:
    if account_id is None:
        return
    from honestspend.services.autopay import recompute_card_payment_schedule

    recompute_card_payment_schedule(session, int(account_id), as_of=as_of)


def _take_new_card(
    session: Session,
    offer: PromoInstallmentLine,
    terms: dict,
    *,
    decision: str,
    as_of: date,
) -> tuple[Account, PromoInstallmentLine | None, list[Transaction]]:
    name = (terms.get("nickname") or offer.name or "New card").strip()
    policy = "statement"
    if decision == "take_with_plan":
        policy = "statement"
    acct = _create_credit_account(
        session, terms=terms, name=name, as_of=as_of, policy=policy
    )
    _apply_policy_if_needed(acct, decision=decision)
    months = int(terms.get("months") or 0)
    end = terms.get("end_date") or offer.end_date
    if end is None and months > 0:
        end = _add_months(as_of, months)
    intro_apr = terms.get("intro_apr")
    if intro_apr in (None, ""):
        intro_apr = terms.get("apr", offer.apr)
    intro = _open_intro(
        session,
        acct,
        name=f"Intro 0% · {acct.nickname}",
        principal=ZERO,
        as_of=as_of,
        end=end,
        apr=intro_apr,
    )
    offer.account_id = acct.id
    acct.promo_apr = _d(intro_apr) if intro_apr not in (None, "") else ZERO
    acct.promo_end_date = end
    _recompute(session, acct.id, as_of)
    return acct, intro, []


def _take_bt(
    session: Session,
    offer: PromoInstallmentLine,
    terms: dict,
    *,
    decision: str,
    as_of: date,
) -> tuple[Account, PromoInstallmentLine | None, list[Transaction]]:
    from honestspend.services.account_balance import apply_amount_to_account

    from_id = _as_int(terms.get("from_account_id"))
    dest_id = _as_int(terms.get("destination_account_id") or terms.get("account_id"))
    src = session.get(Account, from_id) if from_id else None
    if src is None:
        raise ValueError("Balance transfer needs from_account_id")
    dest = session.get(Account, dest_id) if dest_id else None
    if dest is None:
        dest = _create_credit_account(
            session,
            terms=terms,
            name=(terms.get("nickname") or offer.name or "BT card").strip(),
            as_of=as_of,
        )
    amount = _q(terms.get("amount") or 0)
    if amount <= ZERO:
        raise ValueError("Balance transfer amount must be positive")
    fee = _fee_amount(amount, terms)
    pid = _profile_id(session, terms, dest, src)

    txns: list[Transaction] = []
    t_from = Transaction(
        profile_id=pid,
        account_id=src.id,
        txn_date=as_of,
        amount=_q(amount),
        payee="Balance transfer",
        memo=f"BT to {dest.nickname}",
        status="cleared",
        is_transfer=True,
    )
    session.add(t_from)
    session.flush()
    t_dest = Transaction(
        profile_id=pid,
        account_id=dest.id,
        txn_date=as_of,
        amount=-_q(amount),
        payee="Balance transfer",
        memo=f"BT from {src.nickname}",
        status="cleared",
        is_transfer=True,
        transfer_pair_id=t_from.id,
    )
    session.add(t_dest)
    session.flush()
    t_from.transfer_pair_id = t_dest.id
    txns.extend([t_from, t_dest])

    if fee > ZERO:
        t_fee = Transaction(
            profile_id=pid,
            account_id=dest.id,
            txn_date=as_of,
            amount=-_q(fee),
            payee="Balance transfer fee",
            memo=f"{offer.name} fee",
            status="cleared",
            is_transfer=False,
            fee_status="confirmed_fee",
        )
        session.add(t_fee)
        session.flush()
        txns.append(t_fee)

    apply_amount_to_account(src, _q(amount), session=session, recompute=False)
    apply_amount_to_account(dest, -_q(amount), session=session, recompute=False)
    if fee > ZERO:
        apply_amount_to_account(dest, -_q(fee), session=session, recompute=False)

    _apply_policy_if_needed(dest, decision=decision)

    months = int(terms.get("months") or 0)
    end = terms.get("end_date") or offer.end_date
    if end is None and months > 0:
        end = _add_months(as_of, months)
    intro_apr = terms.get("intro_apr")
    if intro_apr in (None, ""):
        intro_apr = terms.get("apr", ZERO)
    intro = _open_intro(
        session,
        dest,
        name=f"Intro 0% BT · {offer.name}",
        principal=amount,
        as_of=as_of,
        end=end,
        apr=intro_apr,
    )
    dest.promo_apr = _d(intro_apr) if intro_apr not in (None, "") else ZERO
    dest.promo_end_date = end
    offer.account_id = dest.id
    _recompute(session, dest.id, as_of)
    _recompute(session, src.id, as_of)
    return dest, intro, txns


def _take_plan(
    session: Session,
    offer: PromoInstallmentLine,
    terms: dict,
    *,
    decision: str,
    as_of: date,
) -> tuple[Account, PromoInstallmentLine | None, list[Transaction]]:
    acct_id = _as_int(terms.get("account_id") or terms.get("destination_account_id"))
    acct = session.get(Account, acct_id) if acct_id else None
    if acct is None:
        raise ValueError("Purchase plan offer needs account_id")
    amount = _q(terms.get("amount") or 0)
    monthly = _q(terms.get("monthly") or 0)
    months = int(terms.get("months") or 0)
    if monthly <= ZERO and months > 0 and amount > ZERO:
        monthly = (amount / Decimal(months)).quantize(CENT, rounding=ROUND_HALF_UP)
    end = terms.get("end_date") or offer.end_date
    if end is None and months > 0:
        end = _add_months(as_of, months)
    _apply_policy_if_needed(acct, decision=decision)
    plan = create_promo_line(
        session,
        acct.id,
        name=offer.name or "Purchase plan",
        principal_remaining=amount,
        monthly_payment=monthly,
        start_date=as_of,
        end_date=end,
        source="user",
        kind="purchase_plan",
        status="open",
        active=True,
    )
    offer.account_id = acct.id
    _recompute(session, acct.id, as_of)
    return acct, plan, []


def decide_offer(
    session: Session,
    offer_id: int,
    *,
    decision: str,
    confirm_skip: bool = False,
) -> dict:
    """take | take_with_plan | skip.

    Verdict from rank_charge + fee vs estimated interest.
    skip without confirm_skip if verdict is Take → still allowed.
    take new_card: create credit Account + card_intro open line; offer status=inactive.
    take BT: fee txn + transfer/principal move + intro on destination.
    take purchase_plan: create open purchase_plan on account.
    take_with_plan: same but set autopay_policy to statement or promo_sink when books would kill 0%.
    """
    del confirm_skip  # skip is always allowed, even when verdict is take
    offer = session.get(PromoInstallmentLine, offer_id)
    if offer is None:
        raise ValueError(f"Offer {offer_id} not found")
    if (offer.kind or "") != "offer":
        raise ValueError(f"Promo line {offer_id} is not an offer")
    act = (decision or "").strip().lower()
    if act not in DECISIONS:
        raise ValueError(f"Unknown decision {decision!r}; use take | take_with_plan | skip")

    verdict = offer_verdict(session, offer)
    terms = terms_from_offer(offer)
    as_of = terms.get("start_date") or offer.start_date or date.today()

    if act == "skip":
        offer.status = "declined"
        offer.active = False
        session.flush()
        return {
            "decision": "skip",
            "offer": offer_to_dict(offer),
            "verdict": verdict,
            "account_id": offer.account_id,
            "account": None,
            "transactions": [],
            "promo_line": None,
        }

    if (offer.status or "") not in ("pending", "", None):
        raise ValueError(f"Offer {offer_id} is already {offer.status}")

    ot = _norm_offer_type(str(terms.get("offer_type") or offer.offer_type or "new_card"))
    if ot == "new_card":
        acct, promo_line, txns = _take_new_card(
            session, offer, terms, decision=act, as_of=as_of
        )
    elif ot == "balance_transfer":
        acct, promo_line, txns = _take_bt(
            session, offer, terms, decision=act, as_of=as_of
        )
    else:
        acct, promo_line, txns = _take_plan(
            session, offer, terms, decision=act, as_of=as_of
        )

    offer.status = "inactive"
    offer.active = False
    session.flush()
    return {
        "decision": act,
        "offer": offer_to_dict(offer),
        "verdict": verdict,
        "account_id": acct.id if acct is not None else offer.account_id,
        "account": _acct_dict(acct),
        "transactions": [_txn_dict(t) for t in txns],
        "promo_line": line_to_dict(promo_line) if promo_line else None,
    }
