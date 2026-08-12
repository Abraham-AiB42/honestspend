"""Smart categorization: deterministic rules first, optional multi-LLM second."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from honestspend.config import settings
from honestspend.db import Category, CategoryRule, Profile, Transaction


@dataclass
class Suggestion:
    category_id: int | None
    category_code: str | None
    category_name: str | None
    confidence: float
    source: str  # rule|llm|grok|openai|anthropic|custom|none
    reason: str
    is_transfer: bool = False
    rule_id: int | None = None


def _haystack(txn: Transaction) -> str:
    return f"{txn.payee or ''} {txn.memo or ''}".strip().lower()


def match_rule(rule: CategoryRule, text: str) -> bool:
    pat = (rule.pattern or "").strip().lower()
    if not pat:
        return False
    mt = (rule.match_type or "contains").lower()
    if mt == "exact":
        return text == pat or text.strip() == pat
    if mt == "starts_with":
        return text.startswith(pat)
    if mt == "regex":
        try:
            return re.search(pat, text, re.I) is not None
        except re.error:
            return False
    # contains (default)
    return pat in text


def suggest_from_rules(
    session: Session,
    txn: Transaction,
) -> Suggestion | None:
    q = session.query(CategoryRule).filter(CategoryRule.active.is_(True))
    # Profile-specific first via priority; null profile_id = global
    rules = q.order_by(CategoryRule.priority.desc(), CategoryRule.id.asc()).all()
    text = _haystack(txn)
    if not text:
        return None

    for rule in rules:
        if rule.profile_id is not None and rule.profile_id != txn.profile_id:
            continue
        if not match_rule(rule, text):
            continue
        cat = session.get(Category, rule.category_id)
        if not cat:
            continue
        # Prefer categories for this profile or system
        if cat.profile_id and cat.profile_id != txn.profile_id:
            continue
        return Suggestion(
            category_id=cat.id,
            category_code=cat.code,
            category_name=cat.display_name,
            confidence=0.95,
            source="rule",
            reason=f"Rule #{rule.id}: {rule.match_type} '{rule.pattern}'",
            is_transfer=bool(rule.is_transfer),
            rule_id=rule.id,
        )
    return None


def _build_categorize_prompt(txn: Transaction, candidates: list[Category]) -> dict[str, Any]:
    cat_lines = [
        {
            "id": c.id,
            "code": c.code,
            "name": c.display_name,
            "tax_form": c.tax_form,
            "tax_line": c.tax_line,
            "budget_group": c.budget_group,
        }
        for c in candidates[:80]
    ]
    return {
        "task": "Pick the best category for this personal/business finance transaction.",
        "transaction": {
            "date": txn.txn_date.isoformat() if txn.txn_date else None,
            "amount": str(txn.amount),
            "payee": txn.payee,
            "memo": txn.memo,
        },
        "categories": cat_lines,
        "rules": [
            "Prefer specific over Uncategorized.",
            "Transfers/payments between own accounts → transfer categories.",
            "Business software/cloud → Software / SaaS when business profile.",
            "Return JSON only: {category_id, confidence 0-1, reason, is_transfer bool}",
        ],
    }


def _chat_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
) -> str:
    with httpx.Client(timeout=45.0) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _chat_anthropic(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        url = f"{root}/messages"
    else:
        url = f"{root}/v1/messages"
    with httpx.Client(timeout=45.0) as client:
        resp = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 512,
                "temperature": 0.1,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data.get("content") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        if texts:
            return "\n".join(texts)
        # Fallback older shapes
        if parts and isinstance(parts[0], dict):
            return str(parts[0].get("text") or "")
        raise ValueError("Empty Anthropic response")


def suggest_from_llm(
    session: Session,
    txn: Transaction,
    *,
    candidates: list[Category],
) -> Suggestion | None:
    """Try configured LLM providers in priority order (xAI → OpenAI → Anthropic → custom)."""
    if not candidates:
        return None
    try:
        from honestspend.services.secrets_store import list_ai_runtime_credentials

        providers = list_ai_runtime_credentials()
    except Exception:
        providers = []
    if not providers:
        return None

    prompt = _build_categorize_prompt(txn, candidates)
    system = (
        "You categorize bank transactions for a multi-entity financial OS. "
        "Reply with JSON only."
    )
    user = json.dumps(prompt)
    errors: list[str] = []

    for prov in providers:
        pid = prov["id"]
        source = "grok" if pid == "xai" else pid
        try:
            if pid == "anthropic":
                content = _chat_anthropic(
                    base_url=prov["base_url"],
                    api_key=prov["api_key"],
                    model=prov["model"],
                    system=system,
                    user=user,
                )
            else:
                content = _chat_openai_compatible(
                    base_url=prov["base_url"],
                    api_key=prov["api_key"],
                    model=prov["model"],
                    system=system,
                    user=user,
                )
            parsed = _parse_json_object(content)
            cid = parsed.get("category_id")
            if cid is None:
                errors.append(f"{pid}: no category_id")
                continue
            cid = int(cid)
            cat = session.get(Category, cid)
            if not cat:
                errors.append(f"{pid}: unknown category {cid}")
                continue
            conf = float(parsed.get("confidence") or 0.5)
            return Suggestion(
                category_id=cat.id,
                category_code=cat.code,
                category_name=cat.display_name,
                confidence=max(0.0, min(1.0, conf)),
                source=source,
                reason=str(parsed.get("reason") or f"{pid} suggestion"),
                is_transfer=bool(parsed.get("is_transfer")),
            )
        except Exception as e:
            errors.append(f"{pid}: {e}")
            continue

    if errors:
        return Suggestion(
            category_id=None,
            category_code=None,
            category_name=None,
            confidence=0.0,
            source="none",
            reason="LLM unavailable: " + "; ".join(errors[:3]),
        )
    return None


def suggest_from_grok(
    session: Session,
    txn: Transaction,
    *,
    candidates: list[Category],
) -> Suggestion | None:
    """Backward-compatible alias → multi-provider LLM."""
    return suggest_from_llm(session, txn, candidates=candidates)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def suggest_category(
    session: Session,
    txn: Transaction,
    *,
    use_grok: bool = True,
    use_llm: bool | None = None,
) -> Suggestion:
    hit = suggest_from_rules(session, txn)
    if hit:
        return hit

    want_llm = use_llm if use_llm is not None else use_grok
    if want_llm:
        try:
            from honestspend.services.secrets_store import any_llm_configured

            llm_ok = any_llm_configured()
        except Exception:
            llm_ok = settings.grok_enabled
        if llm_ok:
            candidates = (
                session.query(Category)
                .filter(
                    (Category.profile_id == txn.profile_id)
                    | (Category.profile_id.is_(None))
                    | (Category.scope == "system")
                )
                .all()
            )
            llm = suggest_from_llm(session, txn, candidates=candidates)
            if llm and llm.category_id:
                return llm
            if llm:
                return llm

    return Suggestion(
        category_id=None,
        category_code=None,
        category_name=None,
        confidence=0.0,
        source="none",
        reason="No rule or LLM match",
    )


def apply_suggestion(
    session: Session,
    txn: Transaction,
    suggestion: Suggestion,
    *,
    min_confidence: float | None = None,
) -> bool:
    floor = (
        min_confidence
        if min_confidence is not None
        else settings.auto_apply_min_confidence
    )
    if not suggestion.category_id or suggestion.confidence < floor:
        return False
    txn.category_id = suggestion.category_id
    txn.confidence = Decimal(str(round(suggestion.confidence, 4)))
    if suggestion.is_transfer:
        txn.is_transfer = True
    return True


def categorize_uncategorized(
    session: Session,
    *,
    profile_id: int | None = None,
    limit: int = 200,
    apply: bool = False,
    use_grok: bool = True,
    min_confidence: float | None = None,
) -> list[dict]:
    q = session.query(Transaction).filter(Transaction.category_id.is_(None))
    if profile_id is not None:
        q = q.filter(Transaction.profile_id == profile_id)
    txns = q.order_by(Transaction.txn_date.desc()).limit(limit).all()

    results = []
    for txn in txns:
        sug = suggest_category(session, txn, use_grok=use_grok)
        applied = False
        if apply:
            applied = apply_suggestion(session, txn, sug, min_confidence=min_confidence)
        results.append(
            {
                "transaction_id": txn.id,
                "txn_date": txn.txn_date.isoformat() if txn.txn_date else None,
                "amount": str(txn.amount),
                "payee": txn.payee,
                "memo": txn.memo,
                "suggestion": {
                    "category_id": sug.category_id,
                    "category_code": sug.category_code,
                    "category_name": sug.category_name,
                    "confidence": sug.confidence,
                    "source": sug.source,
                    "reason": sug.reason,
                    "is_transfer": sug.is_transfer,
                    "rule_id": sug.rule_id,
                },
                "applied": applied,
            }
        )
    if apply:
        session.flush()
    return results


def learn_rule_from_correction(
    session: Session,
    txn: Transaction,
    category_id: int,
    *,
    pattern: str | None = None,
) -> CategoryRule | None:
    """When user assigns a category, create/strengthen a contains rule on payee."""
    payee = (txn.payee or "").strip()
    if not payee and not pattern:
        return None
    pat = (pattern or payee).strip()
    # Skip very short / noisy
    if len(pat) < 3:
        return None

    existing = (
        session.query(CategoryRule)
        .filter(
            CategoryRule.pattern == pat,
            CategoryRule.category_id == category_id,
            CategoryRule.active.is_(True),
        )
        .first()
    )
    if existing:
        return existing

    rule = CategoryRule(
        profile_id=txn.profile_id,
        match_type="contains",
        pattern=pat,
        category_id=category_id,
        priority=150,
        source="learned",
        notes=f"Learned from txn #{txn.id}",
    )
    session.add(rule)
    session.flush()
    return rule


DEFAULT_RULE_SEEDS: list[tuple[str, str, str, int]] = [
    # pattern, match_type, category_code (personal), priority
    ("shell", "contains", "PER_GAS", 100),
    ("chevron", "contains", "PER_GAS", 100),
    ("exxon", "contains", "PER_GAS", 100),
    ("costco gas", "contains", "PER_GAS", 120),
    ("costco", "contains", "PER_GROCERIES", 80),
    ("walmart", "contains", "PER_SHOPPING", 80),
    ("target", "contains", "PER_SHOPPING", 80),
    ("amazon", "contains", "PER_SHOPPING", 70),
    ("amzn", "contains", "PER_SHOPPING", 70),
    ("whole foods", "contains", "PER_GROCERIES", 100),
    ("kroger", "contains", "PER_GROCERIES", 100),
    ("safeway", "contains", "PER_GROCERIES", 100),
    ("mcdonald", "contains", "PER_DINING", 100),
    ("starbucks", "contains", "PER_DINING", 100),
    ("chipotle", "contains", "PER_DINING", 100),
    ("uber eats", "contains", "PER_DINING", 110),
    ("doordash", "contains", "PER_DINING", 110),
    ("netflix", "contains", "PER_SUBSCRIPTIONS", 100),
    ("spotify", "contains", "PER_SUBSCRIPTIONS", 100),
    ("hulu", "contains", "PER_SUBSCRIPTIONS", 100),
    ("disney+", "contains", "PER_SUBSCRIPTIONS", 100),
    ("verizon", "contains", "PER_CELL", 100),
    ("t-mobile", "contains", "PER_CELL", 100),
    ("at&t", "contains", "PER_CELL", 100),
    ("xcel energy", "contains", "PER_UTILITIES", 100),
    ("comcast", "contains", "PER_UTILITIES", 100),
    ("xfinity", "contains", "PER_UTILITIES", 100),
    ("geico", "contains", "PER_INSURANCE", 100),
    ("state farm", "contains", "PER_INSURANCE", 100),
    ("progressive", "contains", "PER_INSURANCE", 100),
    ("payment thank you", "contains", "SYS_CC_PAYMENT", 200),
    ("autopay", "contains", "SYS_CC_PAYMENT", 180),
    ("credit card payment", "contains", "SYS_CC_PAYMENT", 200),
    ("transfer", "contains", "SYS_TRANSFER", 90),
    ("venmo", "contains", "SYS_TRANSFER", 80),
    ("zelle", "contains", "SYS_TRANSFER", 80),
]


BUSINESS_RULE_SEEDS: list[tuple[str, str, str, int]] = [
    ("azure", "contains", "BIZ_OTHER_SOFTWARE", 120),
    ("microsoft", "contains", "BIZ_OTHER_SOFTWARE", 90),
    ("openai", "contains", "BIZ_OTHER_SOFTWARE", 120),
    ("x.ai", "contains", "BIZ_OTHER_SOFTWARE", 120),
    ("stripe", "contains", "BIZ_OTHER_BANK_FEES", 100),
    ("aws", "contains", "BIZ_OTHER_SOFTWARE", 110),
    ("google cloud", "contains", "BIZ_OTHER_SOFTWARE", 110),
    ("godaddy", "contains", "BIZ_OTHER_SOFTWARE", 100),
    ("namecheap", "contains", "BIZ_OTHER_SOFTWARE", 100),
    ("linkedin ads", "contains", "BIZ_ADVERTISING", 110),
    ("meta ads", "contains", "BIZ_ADVERTISING", 110),
    ("google ads", "contains", "BIZ_ADVERTISING", 110),
]


def seed_rules_for_profile(session: Session, profile: Profile) -> int:
    """Add template rules for a business/child profile (idempotent per profile)."""
    et = (profile.entity_type or "").lower()
    if et not in ("business", "s_corp", "llc"):
        # child: light rules later; personal handled at seed
        if et == "child":
            return 0
        if et in ("individual", "personal"):
            return 0
        # treat unknown business-like as business if tax form is business
        if (profile.tax_form_primary or "").upper() not in ("1120S", "1065", "SCHC", "1120"):
            return 0

    created = 0
    for pattern, mt, code_base, prio in BUSINESS_RULE_SEEDS:
        cat = (
            session.query(Category)
            .filter(
                Category.profile_id == profile.id,
                Category.code.like(f"{code_base}%"),
            )
            .first()
        )
        if not cat:
            continue
        exists = (
            session.query(CategoryRule)
            .filter(
                CategoryRule.profile_id == profile.id,
                CategoryRule.pattern == pattern,
                CategoryRule.source == "seed",
            )
            .count()
        )
        if exists:
            continue
        session.add(
            CategoryRule(
                profile_id=profile.id,
                match_type=mt,
                pattern=pattern,
                category_id=cat.id,
                priority=prio,
                source="seed",
            )
        )
        created += 1
    session.flush()
    return created


def seed_default_rules(session: Session) -> int:
    """Seed personal + system rules once. Business rules attach when profiles are created."""
    if session.query(CategoryRule).filter(CategoryRule.source == "seed").count() > 0:
        return 0

    created = 0
    personal = session.query(Profile).filter(Profile.slug == "personal").one_or_none()
    if personal:
        for pattern, mt, code, prio in DEFAULT_RULE_SEEDS:
            cat = session.query(Category).filter(Category.code == code).first()
            if not cat:
                continue
            session.add(
                CategoryRule(
                    profile_id=personal.id if not code.startswith("SYS_") else None,
                    match_type=mt,
                    pattern=pattern,
                    category_id=cat.id,
                    priority=prio,
                    is_transfer=code.startswith("SYS_TRANSFER") or code.startswith("SYS_CC"),
                    source="seed",
                )
            )
            created += 1

    # Any pre-existing business profiles (legacy DBs)
    for prof in session.query(Profile).all():
        if prof.slug == "personal":
            continue
        et = (prof.entity_type or "").lower()
        if et in ("business", "s_corp", "llc") or (prof.tax_form_primary or "").upper() in (
            "1120S",
            "1065",
            "1120",
        ):
            created += seed_rules_for_profile(session, prof)

    session.flush()
    return created
