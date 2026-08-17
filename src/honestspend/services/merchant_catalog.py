"""Deterministic category hints from payee text and bank-supplied labels.

National brands and issuer category names only. Never encode household names,
account tails, addresses, or live download filenames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from honestspend.db import Category, Profile


@dataclass(frozen=True)
class CatalogHit:
    code: str
    confidence: float
    reason: str
    is_transfer: bool = False


def format_import_memo(
    source: str,
    filename: str,
    *,
    category: str = "",
    txn_type: str = "",
) -> str:
    """Keep issuer Category/Type on the txn so first-pass matching can use them."""
    bits = [f"{source}:{(filename or 'import')[:120]}"]
    cat = re.sub(r"[\r\n|]+", " ", category or "").strip()
    typ = re.sub(r"[\r\n|]+", " ", txn_type or "").strip()
    if cat:
        bits.append(f"cat:{cat[:80]}")
    if typ:
        bits.append(f"type:{typ[:40]}")
    return " · ".join(bits)[:500]


def parse_import_hints(memo: str | None) -> tuple[str, str]:
    cat = typ = ""
    for part in (memo or "").split("·"):
        p = part.strip()
        low = p.lower()
        if low.startswith("cat:"):
            cat = p[4:].strip()
        elif low.startswith("type:"):
            typ = p[5:].strip()
    return cat, typ


def _norm(text: str) -> str:
    s = (text or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[*_#]+", " ", s)
    s = re.sub(r"[^a-z0-9+./ -]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9+]+", _norm(text)) if len(t) > 1}


def _is_business(session: Session, profile_id: int | None) -> bool:
    if not profile_id:
        return False
    p = session.get(Profile, profile_id)
    if not p:
        return False
    et = (p.entity_type or "").strip().lower()
    if et in ("business", "s_corp", "llc", "partnership", "c_corp"):
        return True
    return (p.tax_form_primary or "").upper() in ("1120S", "1065", "SCHC", "1120")


def resolve_category(
    session: Session,
    code: str,
    *,
    profile_id: int | None,
) -> Category | None:
    if not code:
        return None
    if code.startswith("SYS_"):
        return session.query(Category).filter(Category.code == code).first()
    if code.startswith("BIZ_"):
        q = session.query(Category).filter(Category.code.like(f"{code}%"))
        if profile_id is not None:
            hit = q.filter(Category.profile_id == profile_id).first()
            if hit:
                return hit
        return q.first()
    q = session.query(Category).filter(Category.code == code)
    if profile_id is not None:
        hit = q.filter(
            (Category.profile_id == profile_id) | (Category.profile_id.is_(None))
        ).first()
        if hit:
            return hit
    return q.first()


# ---------------------------------------------------------------------------
# Payee heuristics (payments, transfers, interest, fees) — high confidence
# ---------------------------------------------------------------------------

_CC_BRAND = (
    r"(?:american express|amex|chase|jpmorgan|discover|capital one|citi(?:bank)?"
    r"|apple card|bankcard|1st bankcard|first bankcard|synchrony|barclay"
    r"|wells fargo(?: card)?|us bank card|navy federal|usaa card|credit one)"
)

_PAYMENT_RX = re.compile(
    r"(?:"
    r"\b(?:automatic|auto|online|ach|web|mobile|internet)\s+payments?\b"
    r"|\bpayments?\s*[-–]?\s*(?:thank|thanks)\b"
    r"|\bthank(?:s| you)\b.*\bpayments?\b"
    r"|\bautopay(?:\s+(?:pymt|payment|pyment))?\b"
    r"|\bauto\s+pay(?:ment)?s?\b"
    r"|\bweb payment\b"
    r"|\bpayment/credit\b"
    r"|\bdirect\s*pay(?:ment)?\b"
    r"|\bfull\s+balance\b"
    r"|\bpayment\s+received\b"
    r")",
    re.I,
)

_CC_PAYMENT_TO_RX = re.compile(
    rf"\bpayment to {_CC_BRAND}\b",
    re.I,
)

_CC_AUTOPAY_BRAND_RX = re.compile(
    rf"\b{_CC_BRAND}\s+autopay\b",
    re.I,
)

_MORTGAGE_RX = re.compile(
    r"\b(?:rocket mortgage|pennymac|loan depot|better mortgage|\bmortgage\b)\b",
    re.I,
)

_HOA_RX = re.compile(
    r"\b(?:hoa\b|assoc(?:iation)?\s+pmt|homeowners?\s+assoc)",
    re.I,
)

_LOAN_RX = re.compile(
    r"\b(?:student loan|dept(?:artment)? of education|aidvantage|mohela|navient"
    r"|nelnet|great lakes student|hyundai motor|toyota financial|honda financial"
    r"|ford credit|gm financial|motor finance)\b",
    re.I,
)

_TRANSFER_RX = re.compile(
    r"(?:"
    r"\bfrom share\b|\bto share\b|\bonline transfer\b|\binternal transfer\b"
    r"|\baccount transfer\b|\btransfer to\b|\btransfer from\b"
    r"|\bwire (?:transfer|out|in)\b|\bexternal (?:withdrawal|deposit)\b"
    r"|\bbalance transfer\b(?!\s+fee)"
    r")",
    re.I,
)

_P2P_RX = re.compile(
    r"\b(?:zelle|venmo|cash app|paypal|apple cash|applecard.*?cash|cashapp)\b",
    re.I,
)

_REWARD_RX = re.compile(
    r"\b(?:cash back reward|rewards? (?:credit|redemption)|statement credit"
    r"|credit-cash back)\b",
    re.I,
)

_INTEREST_RX = re.compile(
    r"\b(?:interest charge|finance charge|purchase interest|interest charged"
    r"|cash advance interest|interest paid)\b",
    re.I,
)

_FEE_RX = re.compile(
    r"\b(?:late fee|late payment fee|overdraft|nsf\b|annual fee"
    r"|balance transfer fee|foreign transaction fee|service charge"
    r"|membership fee|atm fee|returned item|insufficient fund)\b",
    re.I,
)

_WAGES_RX = re.compile(
    r"\b(?:direct dep(?:osit)?|dir dep|payroll|salary|paycheck)\b",
    re.I,
)


def is_issuer_payment_payee(payee: str, memo: str = "") -> bool:
    """True for issuer autopay / DirectPay / thank-you — not mortgage/HOA/loan."""
    text = f"{payee or ''} {memo or ''}"
    if not text.strip():
        return False
    if _MORTGAGE_RX.search(text) or _HOA_RX.search(text) or _LOAN_RX.search(text):
        return False
    return bool(
        _PAYMENT_RX.search(text)
        or _CC_PAYMENT_TO_RX.search(text)
        or _CC_AUTOPAY_BRAND_RX.search(text)
    )


def suggest_from_payee_heuristics(payee: str, memo: str = "") -> CatalogHit | None:
    text = f"{payee or ''} {memo or ''}"
    if not text.strip():
        return None

    if _FEE_RX.search(text):
        return CatalogHit("PER_FEES", 0.93, "Fee language in payee", False)
    if _INTEREST_RX.search(text):
        return CatalogHit("PER_CC_INTEREST", 0.93, "Interest / finance charge", False)
    if _REWARD_RX.search(text):
        return CatalogHit("SYS_TRANSFER", 0.9, "Card reward / statement credit", True)
    if _HOA_RX.search(text):
        return CatalogHit("PER_HOA", 0.92, "HOA / association payment", False)
    if _MORTGAGE_RX.search(text):
        return CatalogHit("PER_HOUSING", 0.93, "Mortgage payment", False)
    if _LOAN_RX.search(text):
        return CatalogHit("SYS_LOAN_PRINCIPAL", 0.9, "Loan payment", True)
    if _CC_PAYMENT_TO_RX.search(text) or _CC_AUTOPAY_BRAND_RX.search(text):
        return CatalogHit("SYS_CC_PAYMENT", 0.96, "Payment to a card brand", True)
    if _PAYMENT_RX.search(text):
        return CatalogHit("SYS_CC_PAYMENT", 0.94, "Issuer payment / autopay", True)
    if _TRANSFER_RX.search(text):
        return CatalogHit("SYS_TRANSFER", 0.92, "Share / account transfer", True)
    if _P2P_RX.search(text):
        return CatalogHit("SYS_TRANSFER", 0.84, "P2P app (treat as transfer until you recode)", True)
    if _WAGES_RX.search(text):
        return CatalogHit("PER_INCOME_WAGES", 0.88, "Payroll / direct deposit", False)
    from honestspend.services.bnpl import is_bnpl_payee

    if is_bnpl_payee(text):
        return CatalogHit("PER_SHOPPING", 0.9, "Buy now, pay later installment", False)
    return None


# ---------------------------------------------------------------------------
# Bank-supplied Category / Type columns
# ---------------------------------------------------------------------------

_BANK_LABELS: dict[str, str] = {
    # Payments / transfers / fees
    "payment": "SYS_CC_PAYMENT",
    "payments": "SYS_CC_PAYMENT",
    "payment/credit": "SYS_CC_PAYMENT",
    "credit card payments": "SYS_CC_PAYMENT",
    "credit card payment": "SYS_CC_PAYMENT",
    "transfers": "SYS_TRANSFER",
    "transfer": "SYS_TRANSFER",
    "fees and adjustments": "PER_FEES",
    "fees & adjustments": "PER_FEES",
    "service charges and fees": "PER_FEES",
    "service charges & fees": "PER_FEES",
    "fees": "PER_FEES",
    "interest": "PER_CC_INTEREST",
    # Food
    "food and drink": "PER_DINING",
    "food & drink": "PER_DINING",
    "restaurants": "PER_DINING",
    "dining": "PER_DINING",
    "eating out": "PER_DINING",
    "groceries": "PER_GROCERIES",
    "grocery": "PER_GROCERIES",
    "supermarket": "PER_GROCERIES",
    "supermarkets": "PER_GROCERIES",
    # Transport
    "gas": "PER_GAS",
    "gas stations": "PER_GAS",
    "gas/automotive": "PER_GAS",
    "gasoline": "PER_GAS",
    "automotive": "PER_AUTO",
    "auto and transport": "PER_AUTO",
    "auto & transport": "PER_AUTO",
    "travel and commute": "PER_AUTO",
    "travel & commute": "PER_AUTO",
    # Travel
    "travel": "PER_VACATION",
    "airfare": "PER_VACATION",
    "air travel": "PER_VACATION",
    "lodging": "PER_VACATION",
    "hotels": "PER_VACATION",
    "car rental": "PER_VACATION",
    # Home
    "bills and utilities": "PER_UTILITIES",
    "bills & utilities": "PER_UTILITIES",
    "utilities": "PER_UTILITIES",
    "phone/cable": "PER_CELL",
    "phone and cable": "PER_CELL",
    "internet": "PER_CELL",
    "home": "PER_HOUSING",
    "mortgages": "PER_HOUSING",
    "mortgage": "PER_HOUSING",
    "rent": "PER_HOUSING",
    # Health / insurance
    "health and wellness": "PER_HEALTHCARE",
    "health & wellness": "PER_HEALTHCARE",
    "health care": "PER_HEALTHCARE",
    "healthcare": "PER_HEALTHCARE",
    "pharmacy": "PER_HEALTHCARE",
    "insurance": "PER_INSURANCE",
    # Shopping / lifestyle
    "shopping": "PER_SHOPPING",
    "merchandise": "PER_SHOPPING",
    "merchandise and supplies": "PER_SHOPPING",
    "merchandise & supplies": "PER_SHOPPING",
    "merchandise and inventory": "PER_SHOPPING",
    "merchandise & inventory": "PER_SHOPPING",
    "general merchandise": "PER_SHOPPING",
    "entertainment": "PER_ENTERTAINMENT",
    "subscriptions": "PER_SUBSCRIPTIONS",
    "gifts and donations": "PER_GIFTS",
    "gifts & donations": "PER_GIFTS",
    "charity": "PER_SCHA_CHARITY",
    "education": "PER_KIDS",
    "kids": "PER_KIDS",
    "taxes": "PER_EST_TAX",
    "tax": "PER_EST_TAX",
    "payroll": "PER_INCOME_WAGES",
    "wages paid": "PER_INCOME_WAGES",
    "wages": "PER_INCOME_WAGES",
    "loans": "SYS_LOAN_PRINCIPAL",
    "loan": "SYS_LOAN_PRINCIPAL",
    "telephone services": "PER_CELL",
    "telephone": "PER_CELL",
    "online services": "PER_SUBSCRIPTIONS",
    "office and shipping": "PER_SHOPPING",
    "office & shipping": "PER_SHOPPING",
    "office supplies": "PER_SHOPPING",
    "atm/cash withdrawals": "SYS_TRANSFER",
    "atm": "SYS_TRANSFER",
    "cash withdrawal": "SYS_TRANSFER",
    "cash withdrawals": "SYS_TRANSFER",
    "deposits": "SYS_TRANSFER",
    "deposit": "SYS_TRANSFER",
    "other income": "PER_INCOME_OTHER",
    "investment income": "PER_INVEST",
    "paychecks/salary": "PER_INCOME_WAGES",
    "paycheck": "PER_INCOME_WAGES",
    "refunds/adjustments": "SYS_TRANSFER",
    "refunds": "SYS_TRANSFER",
    # Chase Spending Planner (17 categories) + common issuer extras
    "cash out": "SYS_TRANSFER",
    "cashback": "SYS_TRANSFER",
    "drug stores": "PER_HEALTHCARE",
    "drugstores": "PER_HEALTHCARE",
    "fitness": "PER_HEALTHCARE",
    "gyms": "PER_HEALTHCARE",
    "public transit": "PER_VACATION",
    "public transportation": "PER_VACATION",
    "transportation": "PER_AUTO",
    "streaming": "PER_SUBSCRIPTIONS",
    "streaming services": "PER_SUBSCRIPTIONS",
    "home improvement": "PER_SHOPPING",
    "department stores": "PER_SHOPPING",
    "wholesale clubs": "PER_GROCERIES",
    "wholesale club": "PER_GROCERIES",
    "repair and maintenance": "PER_AUTO",
    "repair & maintenance": "PER_AUTO",
}

_BANK_LABELS_BIZ: dict[str, str] = {
    "office and shipping": "BIZ_OTHER_OFFICE",
    "office & shipping": "BIZ_OTHER_OFFICE",
    "office supplies": "BIZ_OTHER_OFFICE",
    "professional services": "BIZ_OTHER_LEGAL",
    "business services": "BIZ_OTHER_LEGAL",
    "advertising": "BIZ_ADVERTISING",
    "software": "BIZ_OTHER_SOFTWARE",
}

_TYPE_PAYMENT = {
    "payment",
    "payments",
    "credit",
    "payment/credit",
}


def suggest_from_bank_label(
    category: str,
    txn_type: str = "",
    *,
    business: bool = False,
) -> CatalogHit | None:
    typ = _norm(txn_type)
    if typ in _TYPE_PAYMENT or typ == "adjustment" and _PAYMENT_RX.search(category or ""):
        if typ in _TYPE_PAYMENT:
            return CatalogHit("SYS_CC_PAYMENT", 0.9, f"Issuer type “{txn_type}”", True)

    raw = _norm(category)
    if not raw:
        return None
    if business:
        for key, code in _BANK_LABELS_BIZ.items():
            if raw == key or key in raw:
                return CatalogHit(code, 0.86, f"Issuer category “{category}”", False)
    code = _BANK_LABELS.get(raw)
    if code:
        xfer = code.startswith("SYS_")
        return CatalogHit(code, 0.86, f"Issuer category “{category}”", xfer)
    for key, mapped in _BANK_LABELS.items():
        if len(key) >= 5 and (raw == key or key in raw or raw in key):
            xfer = mapped.startswith("SYS_")
            return CatalogHit(mapped, 0.84, f"Issuer category “{category}”", xfer)
    return None


# ---------------------------------------------------------------------------
# National merchant catalog — longer phrases first
# ---------------------------------------------------------------------------

# (needle, personal_code, business_code or None)
# Sources: NRF Top 100 Retailers 2025, Technomic Top 500 restaurants 2026,
# common US issuer statement descriptors, major utilities / streamers / insurers.
# Longer / more specific needles first.
_MERCHANTS: list[tuple[str, str, str | None]] = [
    # --- Gas / EV (before warehouse clubs and 7-Eleven) ---
    ("costco gas", "PER_GAS", None),
    ("sams club gas", "PER_GAS", None),
    ("sam's club gas", "PER_GAS", None),
    ("safeway fuel", "PER_GAS", None),
    ("kroger fuel", "PER_GAS", None),
    ("electrify america", "PER_GAS", None),
    ("tesla supercharger", "PER_GAS", None),
    ("chargepoint", "PER_GAS", None),
    ("evgo", "PER_GAS", None),
    ("loves travel", "PER_GAS", None),
    ("love's", "PER_GAS", None),
    ("flying j", "PER_GAS", None),
    ("phillips 66", "PER_GAS", None),
    ("circle k", "PER_GAS", None),
    ("7-eleven", "PER_GAS", None),
    ("7 eleven", "PER_GAS", None),
    ("kum and go", "PER_GAS", None),
    ("quiktrip", "PER_GAS", None),
    ("racetrac", "PER_GAS", None),
    ("speedway", "PER_GAS", None),
    ("maverik", "PER_GAS", None),
    ("sheetz", "PER_GAS", None),
    ("wawa", "PER_GAS", None),
    ("caseys", "PER_GAS", None),
    ("casey's", "PER_GAS", None),
    ("sinclair", "PER_GAS", None),
    ("marathon", "PER_GAS", None),
    ("sunoco", "PER_GAS", None),
    ("chevron", "PER_GAS", None),
    ("texaco", "PER_GAS", None),
    ("exxon", "PER_GAS", None),
    ("valero", "PER_GAS", None),
    ("conoco", "PER_GAS", None),
    ("arco", "PER_GAS", None),
    ("shell", "PER_GAS", None),
    ("mobil", "PER_GAS", None),
    ("pilot", "PER_GAS", None),
    ("bp", "PER_GAS", None),
    # --- Groceries / warehouse / Kroger + Albertsons + Ahold banners ---
    ("whole foods", "PER_GROCERIES", None),
    ("wholefds", "PER_GROCERIES", None),
    ("trader joe", "PER_GROCERIES", None),
    ("king soopers", "PER_GROCERIES", None),
    ("city market", "PER_GROCERIES", None),
    ("natural grocers", "PER_GROCERIES", None),
    ("vitamin cottage", "PER_GROCERIES", None),
    ("harris teeter", "PER_GROCERIES", None),
    ("food 4 less", "PER_GROCERIES", None),
    ("foods co", "PER_GROCERIES", None),
    ("food lion", "PER_GROCERIES", None),
    ("stop and shop", "PER_GROCERIES", None),
    ("giant eagle", "PER_GROCERIES", None),
    ("giant food", "PER_GROCERIES", None),
    ("hannaford", "PER_GROCERIES", None),
    ("jewel osco", "PER_GROCERIES", None),
    ("star market", "PER_GROCERIES", None),
    ("tom thumb", "PER_GROCERIES", None),
    ("pick n save", "PER_GROCERIES", None),
    ("metro market", "PER_GROCERIES", None),
    ("fred meyer", "PER_GROCERIES", None),
    ("shoprite", "PER_GROCERIES", None),
    ("sprouts", "PER_GROCERIES", None),
    ("safeway", "PER_GROCERIES", None),
    ("albertsons", "PER_GROCERIES", None),
    ("pavilions", "PER_GROCERIES", None),
    ("randalls", "PER_GROCERIES", None),
    ("wegmans", "PER_GROCERIES", None),
    ("hy-vee", "PER_GROCERIES", None),
    ("hyvee", "PER_GROCERIES", None),
    ("winco", "PER_GROCERIES", None),
    ("publix", "PER_GROCERIES", None),
    ("kroger", "PER_GROCERIES", None),
    ("ralphs", "PER_GROCERIES", None),
    ("smiths", "PER_GROCERIES", None),
    ("dillons", "PER_GROCERIES", None),
    ("marianos", "PER_GROCERIES", None),
    ("frys", "PER_GROCERIES", None),
    ("qfc", "PER_GROCERIES", None),
    ("vons", "PER_GROCERIES", None),
    ("acme", "PER_GROCERIES", None),
    ("shaws", "PER_GROCERIES", None),
    ("ingles", "PER_GROCERIES", None),
    ("raley", "PER_GROCERIES", None),
    ("piggly wiggly", "PER_GROCERIES", None),
    ("stater bros", "PER_GROCERIES", None),
    ("meijer", "PER_GROCERIES", None),
    ("aldi", "PER_GROCERIES", None),
    ("lidl", "PER_GROCERIES", None),
    ("heb", "PER_GROCERIES", None),
    ("instacart", "PER_GROCERIES", None),
    ("thrive market", "PER_GROCERIES", None),
    ("gopuff", "PER_GROCERIES", None),
    ("costco whse", "PER_GROCERIES", None),
    ("costco.com", "PER_GROCERIES", None),
    ("costco", "PER_GROCERIES", None),
    ("sam's club", "PER_GROCERIES", None),
    ("sams club", "PER_GROCERIES", None),
    ("bj's", "PER_GROCERIES", None),
    ("bjs wholesale", "PER_GROCERIES", None),
    # --- Dining / delivery (Technomic Top 50 + Toast POS) ---
    ("uber eats", "PER_DINING", None),
    ("doordash", "PER_DINING", None),
    ("grubhub", "PER_DINING", None),
    ("postmates", "PER_DINING", None),
    ("seamless", "PER_DINING", None),
    ("caviar", "PER_DINING", None),
    ("chick-fil-a", "PER_DINING", None),
    ("chick fila", "PER_DINING", None),
    ("chickfila", "PER_DINING", None),
    ("raising cane", "PER_DINING", None),
    ("panda express", "PER_DINING", None),
    ("texas roadhouse", "PER_DINING", None),
    ("olive garden", "PER_DINING", None),
    ("buffalo wild wings", "PER_DINING", None),
    ("cheesecake factory", "PER_DINING", None),
    ("cracker barrel", "PER_DINING", None),
    ("outback steakhouse", "PER_DINING", None),
    ("longhorn steak", "PER_DINING", None),
    ("jersey mike", "PER_DINING", None),
    ("jimmy john", "PER_DINING", None),
    ("little caesar", "PER_DINING", None),
    ("tropical smoothie", "PER_DINING", None),
    ("dutch bros", "PER_DINING", None),
    ("waffle house", "PER_DINING", None),
    ("golden corral", "PER_DINING", None),
    ("red lobster", "PER_DINING", None),
    ("red robin", "PER_DINING", None),
    ("jack in the box", "PER_DINING", None),
    ("whataburger", "PER_DINING", None),
    ("in-n-out", "PER_DINING", None),
    ("in n out", "PER_DINING", None),
    ("shake shack", "PER_DINING", None),
    ("five guys", "PER_DINING", None),
    ("sweetgreen", "PER_DINING", None),
    ("chili s", "PER_DINING", None),
    ("applebee", "PER_DINING", None),
    ("ihop", "PER_DINING", None),
    ("denny s", "PER_DINING", None),
    ("zaxby", "PER_DINING", None),
    ("bojangle", "PER_DINING", None),
    ("hardee", "PER_DINING", None),
    ("wingstop", "PER_DINING", None),
    ("dairy queen", "PER_DINING", None),
    ("pizza hut", "PER_DINING", None),
    ("papa john", "PER_DINING", None),
    ("mcdonald", "PER_DINING", None),
    ("starbucks", "PER_DINING", None),
    ("dunkin", "PER_DINING", None),
    ("chipotle", "PER_DINING", None),
    ("taco bell", "PER_DINING", None),
    ("burger king", "PER_DINING", None),
    ("wendy s", "PER_DINING", None),
    ("panera", "PER_DINING", None),
    ("subway", "PER_DINING", None),
    ("dominos", "PER_DINING", None),
    ("domino", "PER_DINING", None),
    ("rock bottom", "PER_DINING", None),
    ("pizza street", "PER_DINING", None),
    ("popeyes", "PER_DINING", None),
    ("culver", "PER_DINING", None),
    ("sonic", "PER_DINING", None),
    ("arby", "PER_DINING", None),
    ("kfc", "PER_DINING", None),
    ("speakeasy", "PER_DINING", None),
    # --- Shopping / NRF retailers + statement aliases ---
    ("wm supercenter", "PER_SHOPPING", None),
    ("wal-mart", "PER_SHOPPING", None),
    ("walmart.com", "PER_SHOPPING", None),
    ("amzn mktp", "PER_SHOPPING", None),
    ("amzn digital", "PER_SHOPPING", None),
    ("amazon.com", "PER_SHOPPING", None),
    ("amazon prime", "PER_SUBSCRIPTIONS", None),
    ("home depot", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("harbor freight", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("tractor supply", "PER_SHOPPING", None),
    ("hobby lobby", "PER_SHOPPING", None),
    ("michaels", "PER_SHOPPING", None),
    ("sherwin williams", "PER_SHOPPING", None),
    ("ace hardware", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("true value", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("menards", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("dicks sporting", "PER_SHOPPING", None),
    ("academy sports", "PER_SHOPPING", None),
    ("bass pro", "PER_SHOPPING", None),
    ("cabela", "PER_SHOPPING", None),
    ("foot locker", "PER_SHOPPING", None),
    ("bath and body", "PER_SHOPPING", None),
    ("victoria s secret", "PER_SHOPPING", None),
    ("lululemon", "PER_SHOPPING", None),
    ("old navy", "PER_SHOPPING", None),
    ("tj maxx", "PER_SHOPPING", None),
    ("t.j. maxx", "PER_SHOPPING", None),
    ("marshalls", "PER_SHOPPING", None),
    ("ross stores", "PER_SHOPPING", None),
    ("burlington", "PER_SHOPPING", None),
    ("dollar tree", "PER_SHOPPING", None),
    ("dollar general", "PER_SHOPPING", None),
    ("family dollar", "PER_SHOPPING", None),
    ("five below", "PER_SHOPPING", None),
    ("best buy", "PER_SHOPPING", None),
    ("apple store", "PER_SHOPPING", None),
    ("apple.com/bill", "PER_SUBSCRIPTIONS", None),
    ("lowes", "PER_SHOPPING", "BIZ_OTHER_SUPPLIES"),
    ("wayfair", "PER_SHOPPING", None),
    ("chewy", "PER_SHOPPING", None),
    ("petsmart", "PER_SHOPPING", None),
    ("petco", "PER_SHOPPING", None),
    ("staples", "PER_SHOPPING", "BIZ_OTHER_OFFICE"),
    ("office depot", "PER_SHOPPING", "BIZ_OTHER_OFFICE"),
    ("officemax", "PER_SHOPPING", "BIZ_OTHER_OFFICE"),
    ("total wine", "PER_SHOPPING", None),
    ("williams sonoma", "PER_SHOPPING", None),
    ("pottery barn", "PER_SHOPPING", None),
    ("west elm", "PER_SHOPPING", None),
    ("jcpenney", "PER_SHOPPING", None),
    ("jc penney", "PER_SHOPPING", None),
    ("dillard", "PER_SHOPPING", None),
    ("nordstrom", "PER_SHOPPING", None),
    ("macys", "PER_SHOPPING", None),
    ("kohls", "PER_SHOPPING", None),
    ("ikea", "PER_SHOPPING", None),
    ("ulta", "PER_SHOPPING", None),
    ("sephora", "PER_SHOPPING", None),
    ("ebay", "PER_SHOPPING", None),
    ("etsy", "PER_SHOPPING", None),
    ("temu", "PER_SHOPPING", None),
    ("shein", "PER_SHOPPING", None),
    ("klarna", "PER_SHOPPING", None),
    ("affirm", "PER_SHOPPING", None),
    ("afterpay", "PER_SHOPPING", None),
    ("sezzle", "PER_SHOPPING", None),
    ("quadpay", "PER_SHOPPING", None),
    ("zip co", "PER_SHOPPING", None),
    ("zip pay", "PER_SHOPPING", None),
    ("pay in 4", "PER_SHOPPING", None),
    ("payin4", "PER_SHOPPING", None),
    ("nike", "PER_SHOPPING", None),
    ("adidas", "PER_SHOPPING", None),
    ("gap", "PER_SHOPPING", None),
    ("walmart", "PER_SHOPPING", None),
    ("amazon", "PER_SHOPPING", None),
    ("amzn", "PER_SHOPPING", None),
    ("target", "PER_SHOPPING", None),
    ("wmt", "PER_SHOPPING", None),
    # --- Subscriptions / streaming / software ---
    ("amazon web services", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("google cloud", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("prime video", "PER_SUBSCRIPTIONS", None),
    ("youtube premium", "PER_SUBSCRIPTIONS", None),
    ("youtubepremium", "PER_SUBSCRIPTIONS", None),
    ("youtube tv", "PER_SUBSCRIPTIONS", None),
    ("rumble", "PER_SUBSCRIPTIONS", None),
    ("apple tv", "PER_SUBSCRIPTIONS", None),
    ("disney+", "PER_SUBSCRIPTIONS", None),
    ("disney plus", "PER_SUBSCRIPTIONS", None),
    ("paramount+", "PER_SUBSCRIPTIONS", None),
    ("hbo max", "PER_SUBSCRIPTIONS", None),
    ("max.com", "PER_SUBSCRIPTIONS", None),
    ("microsoft", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("docusign", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("usecanopy", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("quickbooks", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("intuit", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("turbotax", "PER_SUBSCRIPTIONS", None),
    ("audible", "PER_SUBSCRIPTIONS", None),
    ("kindle", "PER_SHOPPING", None),
    ("icloud", "PER_SUBSCRIPTIONS", None),
    ("itunes", "PER_SHOPPING", None),
    ("netflix", "PER_SUBSCRIPTIONS", None),
    ("spotify", "PER_SUBSCRIPTIONS", None),
    ("hulu", "PER_SUBSCRIPTIONS", None),
    ("peacock", "PER_SUBSCRIPTIONS", None),
    ("espn+", "PER_SUBSCRIPTIONS", None),
    ("espn plus", "PER_SUBSCRIPTIONS", None),
    ("siriusxm", "PER_SUBSCRIPTIONS", None),
    ("sirius xm", "PER_SUBSCRIPTIONS", None),
    ("adobe", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("openai", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("chatgpt", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("anthropic", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("x.ai", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("github", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("dropbox", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("zoom.us", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("slack", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("notion", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("canva", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("figma", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("grammarly", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("lastpass", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("1password", "PER_SUBSCRIPTIONS", "BIZ_OTHER_SOFTWARE"),
    ("nintendo", "PER_SUBSCRIPTIONS", None),
    ("playstation", "PER_SUBSCRIPTIONS", None),
    ("xbox", "PER_SUBSCRIPTIONS", None),
    ("steam", "PER_ENTERTAINMENT", None),
    ("hbo", "PER_SUBSCRIPTIONS", None),
    # --- Cell / internet / cable ---
    ("xfinity mobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("spectrum mobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("att wireless", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("mint mobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("google fi", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("starlink", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("stalink", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("visible wireless", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("cricket wireless", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("metro pcs", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("metro by t", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("us cellular", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("boost mobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("t-mobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("tmobile", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("at&t", "PER_CELL", "BIZ_OTHER_PHONE"),
    ("verizon", "PER_CELL", "BIZ_OTHER_PHONE"),
    # --- Utilities / trash / water ---
    ("waste management", "PER_UTILITIES", None),
    ("republic services", "PER_UTILITIES", None),
    ("atmos energy", "PER_UTILITIES", None),
    ("xcel energy", "PER_UTILITIES", None),
    ("duke energy", "PER_UTILITIES", None),
    ("dominion energy", "PER_UTILITIES", None),
    ("georgia power", "PER_UTILITIES", None),
    ("florida power", "PER_UTILITIES", None),
    ("pacific gas", "PER_UTILITIES", None),
    ("southern california edison", "PER_UTILITIES", None),
    ("consolidated edison", "PER_UTILITIES", None),
    ("national grid", "PER_UTILITIES", None),
    ("american electric", "PER_UTILITIES", None),
    ("centerpoint", "PER_UTILITIES", None),
    ("eversource", "PER_UTILITIES", None),
    ("constellation energy", "PER_UTILITIES", None),
    ("nextera", "PER_UTILITIES", None),
    ("entergy", "PER_UTILITIES", None),
    ("ameren", "PER_UTILITIES", None),
    ("pg and e", "PER_UTILITIES", None),
    ("pge", "PER_UTILITIES", None),
    ("con edison", "PER_UTILITIES", None),
    ("coned", "PER_UTILITIES", None),
    ("cox comm", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("centurylink", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("frontier comm", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("wow internet", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("google fiber", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("directv", "PER_UTILITIES", None),
    ("dish network", "PER_UTILITIES", None),
    ("comcast", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("xfinity", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("spectrum", "PER_UTILITIES", "BIZ_OTHER_UTILITIES"),
    ("xcel", "PER_UTILITIES", None),
    # --- Insurance ---
    ("farmers insurance", "PER_INSURANCE", None),
    ("farmers ins", "PER_INSURANCE", None),
    ("liberty mutual", "PER_INSURANCE", None),
    ("american family", "PER_INSURANCE", None),
    ("nationwide", "PER_INSURANCE", None),
    ("travelers", "PER_INSURANCE", None),
    ("state farm", "PER_INSURANCE", None),
    ("progressive", "PER_INSURANCE", None),
    ("allstate", "PER_INSURANCE", None),
    ("geico", "PER_INSURANCE", None),
    ("usaa", "PER_INSURANCE", None),
    ("aflac", "PER_INSURANCE", None),
    ("metlife", "PER_INSURANCE", None),
    # --- Healthcare / pharmacy / gyms ---
    ("quest diagnostics", "PER_HEALTHCARE", None),
    ("planet fitness", "PER_HEALTHCARE", None),
    ("anytime fitness", "PER_HEALTHCARE", None),
    ("la fitness", "PER_HEALTHCARE", None),
    ("lifetime fitness", "PER_HEALTHCARE", None),
    ("orange theory", "PER_HEALTHCARE", None),
    ("orangetheory", "PER_HEALTHCARE", None),
    ("warby parker", "PER_HEALTHCARE", None),
    ("lenscrafters", "PER_HEALTHCARE", None),
    ("visionworks", "PER_HEALTHCARE", None),
    ("goodrx", "PER_HEALTHCARE", None),
    ("labcorp", "PER_HEALTHCARE", None),
    ("walgreens", "PER_HEALTHCARE", None),
    ("rite aid", "PER_HEALTHCARE", None),
    ("peloton", "PER_HEALTHCARE", None),
    ("kaiser", "PER_HEALTHCARE", None),
    ("cvs", "PER_HEALTHCARE", None),
    # --- Auto ---
    ("discount tire", "PER_AUTO", None),
    ("jiffy lube", "PER_AUTO", None),
    ("napa auto", "PER_AUTO", None),
    ("motor veh", "PER_AUTO", None),
    ("vehicle reg", "PER_AUTO", None),
    ("registration fee", "PER_AUTO", None),
    ("co motor", "PER_AUTO", None),
    ("dmv", "PER_AUTO", None),
    ("pep boys", "PER_AUTO", None),
    ("valvoline", "PER_AUTO", None),
    ("firestone", "PER_AUTO", None),
    ("goodyear", "PER_AUTO", None),
    ("autozone", "PER_AUTO", None),
    ("o'reilly", "PER_AUTO", None),
    ("oreilly", "PER_AUTO", None),
    ("midas", "PER_AUTO", None),
    ("take 5", "PER_AUTO", None),
    # --- Travel / transit ---
    ("united airlines", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("american airlines", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("alaska air", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("delta air", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("booking.com", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("southwest", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("jetblue", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("spirit air", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("frontier air", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("united air", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("marriott", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("hilton", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("hyatt", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("ihg", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("holiday inn", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("airbnb", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("vrbo", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("expedia", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("hotels.com", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("priceline", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("kayak", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("amtrak", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("enterprise rent", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("hertz", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("avis", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("budget rent", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("national car", "PER_VACATION", "BIZ_OTHER_TRAVEL"),
    ("uber trip", "PER_VACATION", None),
    ("uber", "PER_VACATION", None),
    ("lyft", "PER_VACATION", None),
    # --- Invest ---
    ("robinhood", "PER_INVEST", None),
    ("fidelity", "PER_INVEST", None),
    ("vanguard", "PER_INVEST", None),
    ("schwab", "PER_INVEST", None),
    ("etrade", "PER_INVEST", None),
    ("e-trade", "PER_INVEST", None),
    ("coinbase", "PER_INVEST", None),
    ("acorns", "PER_INVEST", None),
    ("betterment", "PER_INVEST", None),
    ("wealthfront", "PER_INVEST", None),
    # --- Postage / ads ---
    ("united states postal", "PER_SHOPPING", "BIZ_OTHER_POSTAGE"),
    ("stamps.com", "PER_SHOPPING", "BIZ_OTHER_POSTAGE"),
    ("ups store", "PER_SHOPPING", "BIZ_OTHER_POSTAGE"),
    ("google ads", "PER_SHOPPING", "BIZ_ADVERTISING"),
    ("meta ads", "PER_SHOPPING", "BIZ_ADVERTISING"),
    ("linkedin ads", "PER_SHOPPING", "BIZ_ADVERTISING"),
    ("usps", "PER_SHOPPING", "BIZ_OTHER_POSTAGE"),
    ("fedex", "PER_SHOPPING", "BIZ_OTHER_POSTAGE"),
]

# Avoid matching these short tokens as a lone contains
_TOKEN_ONLY = {
    "shell",
    "target",
    "pilot",
    "mobil",
    "cvs",
    "heb",
    "kfc",
    "hbo",
    "gap",
    "nike",
    "uber",
    "lyft",
    "aldi",
    "lidl",
    "amzn",
    "bp",
    "wmt",
    "qfc",
    "acme",
    "vons",
    "ikea",
    "ebay",
    "etsy",
    "temu",
    "shein",
    "ihop",
    "avis",
    "steam",
    "pge",
    "usps",
    "dmv",
}


def _needle_hits(hay: str, toks: set[str], needle: str) -> bool:
    n = _norm(needle)
    if not n:
        return False
    if " " in n or "/" in n or "+" in n or "." in n:
        return n in hay
    if n in _TOKEN_ONLY or len(n) <= 4:
        return n in toks
    # word-ish: require token start or full token (AMZN MKTP, COSTCO WHSE)
    if n in toks:
        return True
    return any(t.startswith(n) and len(t) <= len(n) + 4 for t in toks)


def suggest_from_merchants(
    payee: str,
    memo: str = "",
    *,
    business: bool = False,
) -> CatalogHit | None:
    hay = _norm(f"{payee or ''} {memo or ''}")
    if not hay:
        return None
    toks = _tokens(hay)
    # Toast POS (TST*) is almost always a restaurant on US cards.
    if "tst" in toks and len(toks) > 1:
        return CatalogHit("PER_DINING", 0.84, "Toast POS descriptor", False)
    for needle, personal, biz in _MERCHANTS:
        if not _needle_hits(hay, toks, needle):
            continue
        code = biz if business and biz else personal
        return CatalogHit(code, 0.88, f"Merchant “{needle}”", False)
    if _looks_like_dining(hay):
        return CatalogHit("PER_DINING", 0.84, "Restaurant / dining wording", False)
    return None


_DINING_HINT = re.compile(
    r"\b("
    r"tavern|bistro|brewery|brewing|steakhouse|cantina|speakeasy|"
    r"pizzeria|sushi|ramen|noodle|bbq|barbeque|barbecue|"
    r"diner|bakery|gelato|taproom|gastropub|chophouse|"
    r"grill|cafe|caf[eé]|coffee|pub|pizza|"
    r"restaurant|restaur"
    r")\b",
    re.I,
)


def _looks_like_dining(hay: str) -> bool:
    if re.search(r"restaur", hay or ""):
        return True
    return bool(_DINING_HINT.search(hay or ""))


def suggest_catalog(
    session: Session,
    *,
    payee: str,
    memo: str = "",
    profile_id: int | None = None,
) -> CatalogHit | None:
    """Payee heuristics, then national merchants, then issuer Category/Type."""
    hit = suggest_from_payee_heuristics(payee, memo)
    if hit:
        return hit
    business = _is_business(session, profile_id)
    hit = suggest_from_merchants(payee, memo, business=business)
    if hit:
        return hit
    cat, typ = parse_import_hints(memo)
    return suggest_from_bank_label(cat, typ, business=business)
