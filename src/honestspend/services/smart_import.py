"""Smart multi-file bank import planning for average consumers.

Analyzes CSV / OFX / QFX / QIF / PDF downloads, scores personal vs business,
proposes entity + account mapping, then commits when the user confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, BinaryIO

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile
from honestspend.services.bank_ofx import (
    import_ofx,
    ofx_external_account_key,
    parse_ofx_accounts,
    preview_ofx,
)
from honestspend.services.profiles import (
    apply_coa_for_profile,
    normalize_entity_type,
    unique_slug,
)

# --- Heuristic keyword banks -------------------------------------------------

_BUSINESS_NAME = re.compile(
    r"\b("
    r"llc|l\.l\.c|inc\.?|corp\.?|corporation|company|co\.|ltd|llp|pllc|"
    r"business|biz|dba|studio|agency|consulting|services|enterprises|"
    r"holdings|partners|group|shop|store|salon|clinic|dental|law|"
    r"payroll|vendor|invoice|client\s*payment|quickbooks|square\s*inc|"
    r"stripe|paypal\s*business|merchant|ein\b"
    r")\b",
    re.I,
)
_PERSONAL_NAME = re.compile(
    r"\b("
    r"personal|joint|family|household|checking\s*-\s*personal|"
    r"everyday|consumer|rewards\s*visa|debit\s*card"
    r")\b",
    re.I,
)
_BUSINESS_PAYEE = re.compile(
    r"\b("
    r"adp|gusto|paychex|intuit|quickbooks|square\s*inc|stripe|"
    r"godaddy|aws|amazon\s*web|google\s*workspace|microsoft\s*365|"
    r"office\s*depot|staples|fedex|ups\s*store|wework|regus|"
    r"sba|franchise|wholesale|supplier|vendor|contractor|"
    r"irs\s*usatax|state\s*tax|sales\s*tax|workers\s*comp|"
    r"docusign|cloudflare|indeed\s*jobs|usecanopy|canopy\.com|"
    r"grok\s*xai|\bxai\b|farmers\s*ins\s*commerci|ap\s*agency|"
    r"vestwell|sircon|stateinsura"
    r")\b",
    re.I,
)
_PERSONAL_PAYEE = re.compile(
    r"\b("
    r"netflix|spotify|disney\s*\+|hulu|doordash|uber\s*eats|lyft|"
    r"starbucks|whole\s*foods|trader\s*joe|walmart|target|"
    r"mortgage|rent\s*payment|hoa|pediatric|daycare|school|"
    r"pharmacy|cvs|walgreens|costco|sams\s*club|"
    r"domino'?s|mcdonald|wingstop|qdoba|youtube\s*premium|"
    r"blizzard|recreation\.gov|metrolux|hilton"
    r")\b",
    re.I,
)
_BUSINESS_CATEGORY = re.compile(
    r"office\s*&\s*shipping|professional\s*services|business\s*services|"
    r"advertising|merchandise\s*&\s*inventory|computer\s*supplies|"
    r"insurance\s*services",
    re.I,
)
_PERSONAL_CATEGORY = re.compile(
    r"restaurants?|gasoline|entertainment|groceries|"
    r"bar\s*&\s*caf[eé]|theatrical|shopping",
    re.I,
)
_CREDIT_FILE_HINT = re.compile(
    r"(?:^|[^A-Za-z0-9])("
    r"card|creditcard|visa|amex|american\s*express|mastercard|\bmc\b|"
    r"discover|credit|ccard|sapphire|freedom|reserve|"
    r"surpass|hilton\s*honors|"
    r"activity[_-]?\d{6,8}|[_-]activity[_-]"
    r")(?:[^A-Za-z]|$)",
    re.I,
)
_CREDIT_CONTENT_HINT = re.compile(
    r"credit\s*limit|available\s*credit|automatic\s*payment\s*-\s*thank|"
    r"autopay\s*payment|statement\s*credit|directpay|"
    r"balance\s*transfer|new\s*balance|minimum\s*payment|"
    r"\btype\b.*,\s*sale|\bsale\b.*,\s*payment",
    re.I,
)
_SAVINGS_HINT = re.compile(r"\b(sav|savings|mma|money\s*market|share\s*0?2)\b", re.I)
_LOAN_FILE_HINT = re.compile(
    r"(?:"
    r"loan|mortgage|installment|auto[\s_-]*loan|car[\s_-]*loan|student[\s_-]*loan|"
    r"hyundai[\s_-]*(?:motor|finance|capital|palisade)?|"
    r"toyota[\s_-]*financial|honda[\s_-]*financial|ford[\s_-]*credit|"
    r"gm[\s_-]*financial|nelnet|aidvantage|mohela|"
    r"santander[\s_-]*consumer|ally[\s_-]*auto"
    r")",
    re.I,
)


@dataclass
class ScoreBreakdown:
    business: float = 0.0
    personal: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, side: str, pts: float, reason: str) -> None:
        if side == "business":
            self.business += pts
        else:
            self.personal += pts
        self.reasons.append(reason)

    @property
    def entity_type(self) -> str:
        if self.business > self.personal + 0.15:
            return "business"
        if self.personal > self.business + 0.15:
            return "personal"
        # slight lean
        return "business" if self.business > self.personal else "personal"

    @property
    def confidence(self) -> float:
        total = self.business + self.personal
        if total <= 0:
            return 0.35
        winner = max(self.business, self.personal)
        return min(0.95, 0.4 + (winner / max(total, 0.01)) * 0.5)


_TRANSFERISH = re.compile(
    r"\b(transfer|from share|to share|zelle|withdrawal transfer|deposit transfer)\b",
    re.I,
)
_STRONG_BIZ_PAYEE = re.compile(
    r"\b(adp|gusto|paychex|payroll|docusign|cloudflare|indeed\s*jobs|vestwell)\b",
    re.I,
)


def _score_name_blob(blob: str, score: ScoreBreakdown, weight: float = 1.0) -> None:
    if not blob:
        return
    if _BUSINESS_NAME.search(blob):
        score.add("business", 1.2 * weight, f"Name/org looks business: {blob[:40]}")
    if _PERSONAL_NAME.search(blob):
        score.add("personal", 1.1 * weight, f"Name looks personal: {blob[:40]}")


def _score_payee_list(payees: list[str] | None, score: ScoreBreakdown, weight: float = 1.0) -> None:
    if not payees:
        return
    biz_hits = 0
    per_hits = 0
    strong = 0
    for p in payees:
        if not p or _TRANSFERISH.search(p):
            continue
        if _STRONG_BIZ_PAYEE.search(p):
            strong += 1
        if _BUSINESS_PAYEE.search(p):
            biz_hits += 1
        if _PERSONAL_PAYEE.search(p):
            per_hits += 1
    if strong:
        score.add("business", min(2.6, 0.7 * strong) * weight, f"{strong} payroll/ops payees")
    if biz_hits:
        score.add("business", min(1.6, 0.25 * biz_hits) * weight, f"{biz_hits} business-like payees")
    if per_hits:
        score.add("personal", min(2.0, 0.35 * per_hits) * weight, f"{per_hits} personal-like payees")


def _score_text_blob(blob: str, score: ScoreBreakdown, weight: float = 1.0) -> None:
    """Filename / org / product only — not a concatenated payee dump."""
    _score_name_blob(blob, score, weight)


def _score_filename(name: str, score: ScoreBreakdown) -> None:
    base = (name or "").lower()
    _score_text_blob(base, score, weight=1.4)
    if re.search(r"\b(biz|business|ops|operating|dba)\b", base):
        score.add("business", 0.9, "Filename business hint")


def _score_kind(kind: str, score: ScoreBreakdown) -> None:
    # Kind alone does not decide personal vs business — a Chase ink card
    # and a Hilton card are both credit.
    _ = kind


def _score_categories(categories: list[str] | None, score: ScoreBreakdown) -> None:
    if not categories:
        return
    blob = " | ".join(categories)
    biz = len(_BUSINESS_CATEGORY.findall(blob))
    per = len(_PERSONAL_CATEGORY.findall(blob))
    if biz:
        score.add("business", min(2.2, 0.55 * biz), f"{biz} business-like categories")
    if per:
        score.add("personal", min(2.0, 0.45 * per), f"{per} personal-like categories")


def _score_product(product: str | None, score: ScoreBreakdown) -> None:
    if not product:
        return
    if re.search(r"\bbusiness\b", product, re.I):
        score.add("business", 2.4, f"Product is a business card: {product[:48]}")
    elif re.search(r"\b(hilton|honors|surpass|everyday|freedom|prime\s*visa)\b", product, re.I):
        score.add("personal", 1.6, f"Product looks personal: {product[:48]}")


def classify_account_source(
    *,
    filename: str,
    kind: str = "checking",
    org: str | None = None,
    accttype: str | None = None,
    acctid: str | None = None,
    sample_payees: list[str] | None = None,
    sample_categories: list[str] | None = None,
    product: str | None = None,
) -> dict[str, Any]:
    score = ScoreBreakdown()
    _score_filename(filename, score)
    if org:
        _score_text_blob(org, score, weight=1.3)
    if accttype:
        _score_text_blob(accttype, score, weight=0.5)
    _score_kind(kind, score)
    _score_product(product, score)
    _score_categories(sample_categories, score)
    _score_payee_list(sample_payees, score, weight=1.0)
    et = score.entity_type
    conf = score.confidence
    if not score.reasons:
        score.reasons.append("No strong signals — defaulting to Personal (you can change)")
        et = "personal"
        conf = 0.4
    return {
        "suggested_entity_type": et,
        "confidence": round(conf, 2),
        "reasons": score.reasons[:8],
        "scores": {"business": round(score.business, 2), "personal": round(score.personal, 2)},
    }


def guess_account_kind(
    *,
    filename: str = "",
    raw_text: str = "",
    headers: list[str] | None = None,
    accttype: str | None = None,
    product: str | None = None,
) -> str:
    """Credit vs checking vs savings from the file, not just the filename."""
    blob = " ".join(
        x for x in (filename, raw_text[:4000], " ".join(headers or []), accttype or "", product or "") if x
    )
    at = (accttype or "").strip().upper()
    if at in ("SAVINGS", "MONEYMRKT", "MONEYMARKET"):
        return "savings"
    if at in ("CREDITLINE", "CREDITCARD", "CCARD", "CREDIT"):
        return "credit"
    if product and re.search(r"\bcard\b", product, re.I) and not re.search(r"\bloan\b", product, re.I):
        return "credit"
    if re.search(r"\bloan\s+account\b|personal\s+loan|discover\s+loan", blob, re.I) and not re.search(
        r"\b(credit\s*card|discover\s+it)\b", blob, re.I
    ):
        return "loan"
    if _LOAN_FILE_HINT.search(filename) or re.search(
        r"vehicle\s+description|motor\s+finance|lease\s+term|\bvin\s+number\b|"
        r"hyundai\s+motor|toyota\s+financial|principal\s+balance",
        blob,
        re.I,
    ):
        if not re.search(r"\b(credit\s*card|visa|amex|mastercard|discover\s*card)\b", filename, re.I):
            return "loan"
    if _SAVINGS_HINT.search(filename) and not _CREDIT_FILE_HINT.search(filename):
        return "savings"
    header_blob = " ".join(headers or []).lower()
    if header_blob:
        # Card number column is a strong credit signal.
        if re.search(r"\bcard\s*(no\.?|number|#)\b", header_blob):
            return "credit"
        # Chase/Amex activity: Type = Sale / Payment. Do NOT treat bank
        # "Posting Date" + "Transaction Type" (Debit/Credit) as a card.
        sample = (raw_text or "")[:4000]
        sale_type = bool(re.search(r"(?:,|\t)Sale(?:,|\t|$)|Type,Sale|\bSale\b\s*,\s*-?\d", sample, re.I))
        bank_type_col = bool(re.search(r"transaction\s+type", header_blob))
        if re.search(r"\btype\b", header_blob) and (
            sale_type
            or (
                re.search(r"\b(sale|payment)\b", sample, re.I)
                and not re.search(r"\b(debit|withdrawal|deposit)\b", header_blob)
                and not bank_type_col
            )
        ):
            return "credit"
        if re.search(r"\bcard\b", header_blob) and "transaction date" in header_blob:
            return "credit"
    if re.search(r"student\s*loan|loan\s+agreement|principal\s+balance", blob, re.I) and re.search(
        r"\bloan\b", blob, re.I
    ):
        return "loan"
    if _CREDIT_FILE_HINT.search(filename) or _CREDIT_CONTENT_HINT.search(blob):
        return "credit"
    if at in ("CHECKING", "SHAREDRAFT"):
        return "checking"
    return "checking"


def _nickname(org: str | None, kind: str, acctid: str | None) -> str:
    digits = re.sub(r"\D+", "", acctid or "")
    tail = digits[-4:] if len(digits) >= 4 else (acctid or "")[-4:]
    base = (org or "Bank").strip()[:40]
    k = (kind or "checking").replace("_", " ")
    if tail:
        return f"{base} · {k} · …{tail}"[:80]
    return f"{base} · {k}"[:80]


# Boundary that treats _ - . as separators (filenames like chase_sapphire.csv)
_B = r"(?:^|[^A-Za-z0-9])"
_E = r"(?:[^A-Za-z0-9]|$)"

# Common bank / credit-union labels for filename + free-text sniffing
_BANK_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_B + r"(chase|jpmorgan|jp\s*morgan)(?=\d|[^A-Za-z]|$)", re.I), "Chase"),
    (re.compile(_B + r"(wells\s*fargo|wellsfargo|wf\.com)" + _E, re.I), "Wells Fargo"),
    (re.compile(_B + r"(bank\s*of\s*america|bofa|bof\s*a)" + _E, re.I), "Bank of America"),
    (re.compile(_B + r"(citibank|citicards|citi)" + _E, re.I), "Citi"),
    (re.compile(_B + r"(capital\s*one|capone|capitalone)" + _E, re.I), "Capital One"),
    (re.compile(_B + r"(american\s*express|amex)" + _E, re.I), "American Express"),
    (re.compile(_B + r"(discover\s*card|discover)" + _E, re.I), "Discover"),
    (re.compile(_B + r"(usaa)" + _E, re.I), "USAA"),
    (re.compile(_B + r"(us\s*bank|usbank)" + _E, re.I), "U.S. Bank"),
    (re.compile(_B + r"(pnc)" + _E, re.I), "PNC"),
    (re.compile(_B + r"(td\s*bank|tdbank)" + _E, re.I), "TD Bank"),
    (re.compile(_B + r"(truist|bb&t|suntrust)" + _E, re.I), "Truist"),
    (re.compile(_B + r"(navy\s*federal|nfcu)" + _E, re.I), "Navy Federal"),
    (re.compile(_B + r"(canvascu|canvas\s*cu|canvas)" + _E, re.I), "Canvas Credit Union"),
    (re.compile(_B + r"(ally)" + _E, re.I), "Ally"),
    (re.compile(_B + r"(sofi)" + _E, re.I), "SoFi"),
    (re.compile(_B + r"(schwab)" + _E, re.I), "Schwab"),
    (re.compile(_B + r"(fidelity)" + _E, re.I), "Fidelity"),
    (re.compile(_B + r"(venmo)" + _E, re.I), "Venmo"),
    (re.compile(_B + r"(paypal)" + _E, re.I), "PayPal"),
    (re.compile(_B + r"(apple\s*card)" + _E, re.I), "Apple Card"),
    (re.compile(_B + r"(home\s*depot|homedepot)" + _E, re.I), "Home Depot"),
    (re.compile(_B + r"(target\s*circle|target\s*card|target\s+mastercard|target)" + _E, re.I), "Target"),
    (re.compile(_B + r"(scheels)" + _E, re.I), "Scheels"),
    (re.compile(_B + r"(fnbo|first\s+national\s+bank\s+of\s+omaha)" + _E, re.I), "FNBO"),
]

# Store / product brand — what people actually call the card (before the issuing bank)
_CARD_BRANDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"home\s*depot\s+(credit|card|services)", re.I), "Home Depot"),
    (re.compile(r"target\s*circle|target\s*card|target\s+mastercard", re.I), "Target"),
    (re.compile(r"hilton\s*honors", re.I), "Hilton Honors"),
    (re.compile(r"amazon\s+business\s+prime", re.I), "Amazon Business Prime"),
    (re.compile(r"blue\s+business\s+plus", re.I), "Blue Business Plus"),
    (re.compile(r"blue\s+business\s+cash", re.I), "Blue Business Cash"),
    (re.compile(r"wells\s+fargo\s+reflect|reflect\s+visa", re.I), "Wells Fargo Reflect"),
    (re.compile(r"prime\s+visa|5\s*%\s*back\s+at\s+amazon", re.I), "Prime Visa"),
    (re.compile(r"freedom\s+unlimited", re.I), "Chase Freedom"),
    (re.compile(r"sapphire", re.I), "Chase Sapphire"),
    (re.compile(r"ink\s+business", re.I), "Chase Ink"),
    (re.compile(r"apple\s+card", re.I), "Apple Card"),
    (re.compile(r"scheels", re.I), "Scheels"),
]

_LOAN_HINTS: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"rocket\s+mortgage|quicken\s+loans|\bmortgage\b", re.I), "Home loan", None),
    (re.compile(r"hyundai\s+palisade|palisade\s+hybrid", re.I), "Car loan", "Hyundai Palisade"),
    (re.compile(r"hyundai", re.I), "Car loan", "Hyundai"),
    (re.compile(r"toyota\s+financial|toyota\s+motor", re.I), "Car loan", "Toyota"),
    (re.compile(r"honda\s+financial", re.I), "Car loan", "Honda"),
    (re.compile(r"ford\s+credit", re.I), "Car loan", "Ford"),
    (re.compile(r"gm\s+financial|chevrolet", re.I), "Car loan", "GM"),
    (re.compile(r"discover\s+personal\s+loans|discover\s+loan\s+account|personal\s+loan\s+account", re.I), "Personal loan", "Discover"),
    (re.compile(r"student\s+loan|nelnet|aidvantage|great\s+lakes|mohela", re.I), "Student loan", None),
    (re.compile(r"auto\s+loan|motor\s+finance", re.I), "Car loan", None),
]

_ISSUER_NOT_BRAND = frozenset(
    {"citi", "citibank", "td bank", "synchrony", "comenity", "barclays"}
)
_GENERIC_LABELS = _ISSUER_NOT_BRAND | frozenset(
    {"chase", "american express", "amex", "wells fargo", "capital one", "discover", "credit union", "bank"}
)

_SKIP_OWNER = re.compile(
    r"american express|wells fargo|capital one|page \d|po box|account ending|"
    r"customer care|united states|statement|prepared for|account number|"
    r"payment coupon|make check|credit services",
    re.I,
)
_PERSON_NAME = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)+$")
_BIZ_TOKEN = re.compile(r"\b(LLC|L\.L\.C\.?|INC\.?|CORP\.?|AGENCY|STUDIO|HOLDINGS|DBA)\b", re.I)


def _bank_from_text(text: str) -> str | None:
    if re.search(r"canvas\.org|canvas credit union", text or "", re.I):
        return "Canvas Credit Union"
    # Discover stayed the card brand after the Capital One merger
    if re.search(r"\bdiscover\b", text or "", re.I):
        return "Discover"
    cleaned = re.sub(
        r"(?i)(?:payment|pmt|pymt|pay|ach|autopay|auto\s*pymt|co:)\s+"
        r"(?:to\s+)?[A-Za-z0-9 .&'-]{2,40}",
        " ",
        text or "",
    )
    for pat, label in _BANK_ALIASES:
        if pat.search(cleaned):
            return label
    return None


def guess_card_brand(*blobs: str | None) -> str | None:
    """Store / product name people use (Home Depot, Target) over the issuing bank."""
    text = " ".join(b for b in blobs if b)
    if not text:
        return None
    for pat, label in _CARD_BRANDS:
        if pat.search(text):
            return label
    return None


def guess_loan_label(*blobs: str | None) -> tuple[str | None, str | None]:
    """Return (friendly type, optional detail) e.g. ('Car loan', 'Hyundai')."""
    text = " ".join(b for b in blobs if b)
    if not text:
        return None, None
    for pat, label, detail in _LOAN_HINTS:
        if pat.search(text):
            return label, detail
    return None, None


def guess_bank_label(*blobs: str | None) -> str | None:
    """Brand first, then filename issuer, then body issuer."""
    named = [b for b in blobs if b]
    if not named:
        return None
    brand = guess_card_brand(*named)
    if brand:
        return brand
    first = named[0]
    if "." in first and len(first) < 160:
        hit = _bank_from_text(first)
        if hit:
            return hit
    return _bank_from_text(" ".join(named))


def _title_biz(name: str) -> str:
    small = {"in", "of", "the", "and", "at", "for"}
    keep = {"llc", "dba", "inc", "ap", "usa"}
    parts: list[str] = []
    for i, p in enumerate((name or "").split()):
        low = p.lower().strip(".,")
        if low in keep:
            parts.append(low.upper() if low != "llc" else "LLC")
        elif low in small and i > 0:
            parts.append(low)
        else:
            parts.append(p[:1].upper() + p[1:].lower() if p else p)
    return " ".join(parts).strip()


def extract_owner_names(*blobs: str | None) -> list[str]:
    """Business names printed on statements (not the person's name)."""
    text = "\n".join(b for b in blobs if b)
    if not text:
        return []
    found: list[str] = []

    def _add(raw: str) -> None:
        name = " ".join((raw or "").split())
        name = re.sub(r"[\u00aa\u00ba®™]+", "", name).strip(" ·-")
        last = name.split()[-1] if name.split() else ""
        if last and not _BIZ_TOKEN.search(last):
            name = re.sub(r"\s+[A-Z][A-Za-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][A-Za-z]+$", "", name).strip()
        if len(name) < 3 or len(name) > 48:
            return
        if _SKIP_OWNER.search(name) or _PERSON_NAME.match(name):
            return
        if not _BIZ_TOKEN.search(name) and not name.isupper():
            return
        key = name.lower()
        if any(key == x.lower() for x in found):
            return
        found.append(name)

    for m in re.finditer(
        r"\b([A-Z0-9][A-Z0-9 .,&']{1,40}(?:LLC|L\.L\.C\.?|INC\.?|CORP\.?|AGENCY|STUDIO|HOLDINGS))\b",
        text,
    ):
        _add(m.group(1))
    for m in re.finditer(r"\b(AGENCY(?:\s+[A-Z0-9][A-Z0-9']*){1,6})\b", text):
        _add(m.group(1))
    for m in re.finditer(r"Prepared for\s*\n+\s*([^\n]{3,60})", text, re.I):
        _add(m.group(1))
    for line in text.splitlines()[:25]:
        s = " ".join(line.split())
        if _BIZ_TOKEN.search(s) and len(s) <= 42:
            _add(s)
    return found


def _owner_key(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:32]
    return f"business:{slug or 'business'}"


def friendly_account_name(
    *,
    brand: str | None = None,
    product: str | None = None,
    loan_label: str | None = None,
    loan_detail: str | None = None,
    bank: str | None = None,
    kind: str = "checking",
    last4: str | None = None,
) -> str:
    if loan_label:
        core = f"{loan_label} · {loan_detail}" if loan_detail else loan_label
    else:
        prod = (product or "").split("/")[0].strip()
        prod = re.sub(r"[\u00aa\u00ba®™]+", "", prod)
        prod = re.sub(r"\s+card\s*$", "", prod, flags=re.I).strip()
        core = brand or prod or bank or _kind_label(kind)
        if (bank or "") .lower() in _ISSUER_NOT_BRAND and brand:
            core = brand
    core = (core or "Account").strip()
    if last4 and last4 not in core:
        return f"{core} · …{last4}"[:80]
    return core[:80]


def last4_from_id(acctid: str | None) -> str | None:
    digits = re.sub(r"\D+", "", acctid or "")
    if len(digits) >= 4:
        return digits[-4:]
    return None


def _last4_norm(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def _same_last4(a: Any, b: Any) -> bool:
    ta, tb = _last4_norm(a), _last4_norm(b)
    return bool(ta and ta == tb)


def _blob_has_tail(blob: str, last4: str) -> bool:
    """True when nickname / institution / external id carries this last-4."""
    tail = _last4_norm(last4)
    if not tail or not blob:
        return False
    if tail in blob:
        return True
    return any(_last4_norm(x) == tail for x in re.findall(r"\d{4,}", blob))


def match_tails_from_acctid(acctid: str | None) -> list[str]:
    """Last-4 candidates from OFX ids like 010108888-0002 → 0002 and 8888."""
    if not acctid:
        return []
    tails: list[str] = []
    for part in re.findall(r"\d{4,}", str(acctid)):
        t = part[-4:]
        if t not in tails:
            tails.append(t)
    return tails


def last4_from_text(*blobs: str | None) -> str | None:
    """Pick a plausible account last-4 from filename / statement text."""
    named = [b for b in blobs if b]
    # Filename first so Discover-Statement-20260728-3065.pdf still yields 3065
    # when the PDF body is concatenated after it.
    for blob in named:
        hit = _last4_from_one_blob(blob)
        if hit:
            return hit
    return _last4_from_one_blob(" ".join(named)) if named else None


def _last4_from_one_blob(text: str) -> str | None:
    if not text:
        return None
    # Prefer explicit masks: ****1234, Account Ending 6-44112, XXXXXX3333
    for pat in (
        r"account\s+ending\s+\d-(\d{4,5})\b",
        r"account\s+ending\s+(\d{4,5})\b",
        r"acct(?:ount)?\s+ending\s+(\d{4,5})\b",
        r"account\s+number\s+X+(\d{4,5})\b",
        r"account\s+number\s+(\d{8,})",
        r"statements-(\d{4})",
        r"(?:\*{2,}|x{2,}|…|\.{2,}|ending\s*(?:in)?\s*)(\d{4,5})\b",
        r"X{2,}[\s\-X]*(\d{4,5})\b",
        r"(?:chase|citi|discover|amex|capitalone|scheels|fnbo|signature)[_-]?(\d{4})(?:[^0-9]|$)",
        r"scheels[^\d]{0,40}(\d{4})\b",
        r"(?:acct(?:ount)?|card\s*(?:ending|#|no\.?|number))\D{0,12}(\d{4})\b",
        r"(?:^|[\s:M])(\d{4}):\s+[A-Za-z]",
        r"(?:^|[_-])(\d{4})[_-](?:activity|transactions|allavailable)",
        r"[-_](\d{4})(?:\.[a-z]+)?$",
    ):
        m = re.search(pat, text, re.I)
        if m:
            digits = m.group(1)
            tail = digits[-4:] if len(digits) > 4 else digits
            # Dates and Apple Pay device tokens are not the account last-4
            start = m.start(1)
            window = text[max(0, start - 24) : start].lower()
            if tail.startswith(("19", "20")) and "end" not in window and "acct" not in window and "card" not in window:
                continue
            if "apple pay" in window or "gpay" in window or "google pay" in window:
                continue
            return tail
    return None


def _usable_org(org: str | None) -> str | None:
    o = (org or "").strip()
    if not o:
        return None
    if o.isdigit():
        return None
    if len(o) < 2 or len(o) > 48:
        return None
    # First OFX <NAME> is a payee, not the bank
    if re.search(r"\b(pmt|payment|from share|to share|type assoc|thank you)\b", o, re.I):
        return None
    return o


def _infer_credit_union(raw_text: str) -> bool:
    return bool(re.search(r"\b(?:from|to)\s+share\s+\d", raw_text or "", re.I))


def _kind_label(kind: str | None) -> str:
    k = (kind or "checking").lower()
    return {
        "checking": "Checking",
        "savings": "Savings",
        "credit": "Credit card",
        "cash": "Cash",
    }.get(k, k.replace("_", " ").title())


def _fmt_money(amount: Any) -> str | None:
    if amount is None or amount == "":
        return None
    try:
        from decimal import Decimal as D

        raw = re.sub(r"[^\d.\-]", "", str(amount))
        d = D(raw)
    except Exception:
        return None
    if d < 0:
        return f"(${abs(d):,.2f})"
    return f"${d:,.2f}"


def _format_posting_line(date_s: str | None, payee: str, amount: str | None) -> str:
    bits: list[str] = []
    if date_s:
        bits.append(str(date_s)[:10])
    bits.append(payee or "(no payee)")
    if amount:
        bits.append(amount)
    return "  ".join(bits)


def _sample_postings(rows: list[Any] | None, *, limit: int = 8) -> list[dict[str, str]]:
    """Payee + amount (+ date) for More detail so two cards are easy to tell apart."""
    out: list[dict[str, str]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        payee = str(r.get("payee") or r.get("description") or "").strip()
        amt = _fmt_money(r.get("amount"))
        if not payee and not amt:
            continue
        d = r.get("date") or r.get("txn_date")
        if hasattr(d, "isoformat"):
            date_s = d.isoformat()[:10]
        else:
            date_s = str(d or "")[:10]
        if date_s in ("None", "?", "—"):
            date_s = ""
        line = _format_posting_line(date_s, payee[:48], amt)
        out.append({"date": date_s, "payee": payee[:48], "amount": amt or "", "line": line})
        if len(out) >= limit:
            break
    return out


def _date_range_from_rows(rows: list[dict[str, Any]] | None) -> tuple[str | None, str | None]:
    dates = []
    for r in rows or []:
        d = r.get("txn_date")
        if d is None:
            continue
        if hasattr(d, "isoformat"):
            dates.append(d)
        else:
            try:
                from datetime import date as date_cls

                dates.append(date_cls.fromisoformat(str(d)[:10]))
            except Exception:
                pass
    if not dates:
        return None, None
    return min(dates).isoformat(), max(dates).isoformat()


def enrich_review_fields(
    acc: dict[str, Any],
    *,
    filename: str,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """Add idiot-proof review fields so users don't guess which file is which.

    Fills: bank_label, last4, human_title, review_lines, date_from/to, what_we_will_do.
    """
    kind = acc.get("kind") or "checking"
    org = (acc.get("org") or acc.get("bank_id") or "").strip() or None
    acctid = acc.get("acctid")
    product = (acc.get("product") or "").strip()
    lines = (raw_text or "").splitlines()
    header = "\n".join(lines[:14])
    brand_header = "\n".join(lines[:40])
    fmt0 = (acc.get("file_format") or "").lower()
    txn_export = fmt0 in ("csv", "txt", "ofx", "qfx", "qif")
    brand_src = (product, filename, org or "", acc.get("suggested_nickname") or "")
    if not txn_export:
        brand_src = brand_src + (brand_header,)
    brand = acc.get("brand") or guess_card_brand(*brand_src)
    issuer_src = " ".join(x for x in (org, filename) if x)
    if not txn_export:
        issuer_src = issuer_src + " " + brand_header
    issuer = _bank_from_text(issuer_src) or org
    # Store brand wins over issuing bank for the label people will recognize
    bank = brand or issuer
    last4 = last4_from_id(str(acctid) if acctid else None) or last4_from_text(
        filename, header if not txn_export else filename, str(acctid or "")
    )
    loan_label, loan_detail = acc.get("loan_label"), acc.get("loan_detail")
    gl, gd = guess_loan_label(product, filename, header, org or "", raw_text or "")
    file_says_loan = bool(_LOAN_FILE_HINT.search(filename or "") or kind == "loan")
    look_loan = file_says_loan or acc.get("is_statement") or fmt0 == "pdf" or kind == "loan"
    if look_loan:
        loan_label = loan_label or gl
        loan_detail = loan_detail or gd
        if file_says_loan or kind == "loan":
            kind = "loan"
            acc["kind"] = "loan"
        elif loan_label:
            # A card statement that mentions a mortgage/auto product is not itself a loan
            loan_label, loan_detail = None, None
    owners = []
    if not txn_export:
        owners = extract_owner_names(header, product, acc.get("owner_name") or "")
    elif acc.get("owner_name"):
        owners = [acc["owner_name"]]
    if owners and not acc.get("owner_name"):
        acc["owner_name"] = _title_biz(owners[0])
        acc["suggested_entity_type"] = "business"
    nick = friendly_account_name(
        brand=brand,
        product=product,
        loan_label=loan_label,
        loan_detail=loan_detail,
        bank=bank,
        kind=kind,
        last4=last4,
    )

    d0, d1 = acc.get("date_from"), acc.get("date_to")
    if not d0 and acc.get("rows"):
        d0, d1 = _date_range_from_rows(acc.get("rows"))
    # sample dates from sample payees structure sometimes not present

    n_txn = int(acc.get("transactions_found") or 0)
    bal = acc.get("ledger_balance")
    fmt = (acc.get("file_format") or "file").upper()
    kind_l = loan_label or _kind_label(kind)
    human_title = nick

    lines: list[str] = []
    lines.append(f"File: {filename} ({fmt})")
    if product:
        lines.append(f"Card / product: {product}")
    if brand and brand != issuer:
        lines.append(f"We'll call it: {nick}")
    if issuer and issuer != brand:
        lines.append(f"Issued by: {issuer}")
    elif bank:
        lines.append(f"Bank / institution: {bank}")
    if acc.get("owner_name"):
        lines.append(f"On the statement as: {acc['owner_name']}")
    if last4:
        lines.append(f"Account ending: …{last4}")
    elif acctid and len(str(acctid)) <= 12:
        lines.append(f"Account id hint: {acctid}")
    lines.append(f"Type: {kind_l}")
    if n_txn:
        lines.append(f"Transactions found: {n_txn}")
    if d0 and d1:
        lines.append(f"Date range: {d0} → {d1}")
    elif d0:
        lines.append(f"From: {d0}")
    if bal:
        lines.append(f"Statement / ledger balance: ${bal}")
    postings = acc.get("sample_postings") or []
    if postings:
        bits: list[str] = []
        for p in postings[:3]:
            if isinstance(p, dict):
                pay = str(p.get("payee") or "")[:22]
                amt = p.get("amount") or ""
                bits.append(f"{pay} {amt}".strip())
            elif p:
                bits.append(str(p)[:36])
        if bits:
            lines.append("Sample: " + " · ".join(bits))
    else:
        payees = [p for p in (acc.get("sample_payees") or []) if p]
        if payees:
            lines.append("Sample payees: " + ", ".join(str(p)[:28] for p in payees[:4]))
    if acc.get("skip") or acc.get("error"):
        lines.append(acc.get("error") or "Could not read this file — skip it")
    # Confidence note
    conf = float(acc.get("confidence") or 0.5)
    et = acc.get("suggested_entity_type") or "personal"
    entity_word = "Personal" if et == "personal" else "Business"
    if conf >= 0.7:
        lines.append(f"Guess: {entity_word} books (high confidence)")
    else:
        lines.append(f"Guess: {entity_word} books — change if wrong")

    what = f'We will create account “{nick}” under {entity_word}'
    if acc.get("action") == "match" and acc.get("matched_nickname"):
        what = f'We will match existing “{acc.get("matched_nickname")}”'

    reasons = list(acc.get("reasons") or [])
    if bank and f"Bank label: {bank}" not in reasons:
        reasons.insert(0, f"Bank label: {bank}")
    if last4 and f"Last 4: …{last4}" not in reasons:
        reasons.insert(0 if not bank else 1, f"Last 4: …{last4}")

    acc = {
        **acc,
        "suggested_nickname": nick[:80],
        "bank_label": bank,
        "brand": brand,
        "issuer": issuer,
        "loan_label": loan_label,
        "last4": last4,
        "human_title": human_title[:100],
        "review_lines": lines,
        "date_from": d0,
        "date_to": d1,
        "what_we_will_do": what,
        "reasons": reasons[:10],
        "institution": bank or org,
    }
    return acc


_PAYMENT_NICK = re.compile(
    r"^(payment\s+to|payment\s+from|ach\s+payment|automatic\s+payment|"
    r"online\s+ach|pmt\s+to|pymt\s+to)\b",
    re.I,
)


def _score_account(
    a: Account,
    *,
    kind: str | None,
    last4: str,
    bank_label: str | None,
    loan_label: str | None,
    loan_detail: str | None,
    nickname_hint: str,
) -> int:
    """Higher is better. Kind alone is not enough — need last-4, bank+name, or exact nick."""
    nick = (a.nickname or "").lower()
    inst = (a.institution or "").lower()
    blob = f"{nick} {inst} {a.external_id or ''}".lower()
    ak = (a.kind or "").lower()
    score = 0
    identity = 0
    if kind and ak == kind:
        score += 8
    elif kind and ak and ak != kind:
        score -= 12
    if last4 and _blob_has_tail(blob, last4):
        # Last-4 is identity even when kind was guessed wrong (CSV vs statement).
        score += 36
        identity += 1
        if kind and ak and ak != kind:
            score -= 10
    if loan_detail and loan_detail.lower() in blob:
        score += 32
        identity += 1
    if loan_label:
        tokens = [t for t in re.split(r"\W+", loan_label.lower()) if t and t not in ("loan", "a", "the")]
        if tokens and all(t in blob for t in tokens):
            score += 24
            identity += 1
        elif any(t in blob for t in tokens):
            score += 12
    if bank_label:
        bl = bank_label.lower()
        if bl in blob:
            score += 8 if bl in _GENERIC_LABELS else 16
            if bl not in _GENERIC_LABELS and (not kind or ak == kind):
                identity += 1
    hint = (nickname_hint or "").lower()
    if hint and hint == nick:
        score += 26
        identity += 1
    elif hint:
        words = [w for w in re.split(r"\W+", hint) if len(w) > 3]
        hits = sum(1 for w in words if w in blob)
        if words and hits:
            score += 6 + 4 * hits
            if hits >= 2:
                identity += 1
    if _PAYMENT_NICK.search(a.nickname or "") and identity == 0:
        score -= 40
    if identity == 0:
        return min(score, 19)
    return score


def _match_account(
    session: Session,
    *,
    external_key: str | None,
    acctid: str | None,
    nickname_hint: str,
    kind: str | None = None,
    bank_label: str | None = None,
    loan_label: str | None = None,
    loan_detail: str | None = None,
    last4: str | None = None,
) -> Account | None:
    if external_key:
        hit = (
            session.query(Account)
            .filter(Account.external_id == external_key, Account.archived_at.is_(None))
            .first()
        )
        if hit:
            return hit
    digits = re.sub(r"\D+", "", (last4 or acctid or ""))
    tail = digits[-4:] if len(digits) >= 4 else ""
    best: Account | None = None
    best_s = 0
    for a in session.query(Account).filter(Account.archived_at.is_(None)).all():
        s = _score_account(
            a,
            kind=kind,
            last4=tail,
            bank_label=bank_label,
            loan_label=loan_label,
            loan_detail=loan_detail,
            nickname_hint=nickname_hint,
        )
        if s > best_s:
            best, best_s = a, s
    if best is not None and best_s >= 20:
        return best
    return None


def _match_profile(session: Session, entity_type: str, display_hint: str | None = None) -> Profile | None:
    et = normalize_entity_type(entity_type)
    q = session.query(Profile).filter(Profile.entity_type.in_([et, "individual"] if et == "personal" else [et]))
    profiles = q.order_by(Profile.id.asc()).all()
    if not profiles:
        # also accept legacy personal string
        profiles = (
            session.query(Profile)
            .filter(Profile.entity_type == et)
            .order_by(Profile.id.asc())
            .all()
        )
    if display_hint:
        h = display_hint.lower()
        for p in profiles:
            if h in (p.display_name or "").lower() or h in (p.slug or "").lower():
                return p
    return profiles[0] if profiles else None


def _analyze_ofx_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_ofx import _read_text

    text = _read_text(content)
    accounts = parse_ofx_accounts(text)
    out: list[dict[str, Any]] = []
    for a in accounts:
        payees = [str(r.get("payee") or "") for r in (a.get("rows") or [])[:40]]
        postings = _sample_postings(a.get("rows"))
        org = _usable_org(a.get("org")) or guess_bank_label(filename, text[:2500])
        if not org and _infer_credit_union(text):
            org = "Credit union"
        kind = guess_account_kind(
            filename=filename,
            raw_text=text[:3000],
            accttype=a.get("accttype"),
        ) or (a.get("kind") or "checking")
        cls = classify_account_source(
            filename=filename,
            kind=kind,
            org=org,
            accttype=a.get("accttype"),
            acctid=a.get("acctid"),
            sample_payees=payees,
        )
        nick = _nickname(org, kind, a.get("acctid"))
        d0, d1 = _date_range_from_rows(a.get("rows"))
        rec = {
            "source_key": a.get("external_key") or f"ofx:{(a.get('acctid') or nick)}",
            "file_format": "ofx",
            "acctid": a.get("acctid"),
            "external_key": a.get("external_key"),
            "match_tails": match_tails_from_acctid(a.get("acctid")),
            "kind": kind,
            "org": org,
            "bank_id": a.get("bank_id"),
            "accttype": a.get("accttype"),
            "transactions_found": a.get("transactions_found"),
            "ledger_balance": a.get("ledger_balance"),
            "suggested_nickname": nick,
            "date_from": d0,
            "date_to": d1,
            "rows": a.get("rows"),  # stripped later in plan
            **cls,
            "sample_payees": payees[:6],
            "sample_postings": postings,
            "txn_fps": _fingerprints_from_rows(a.get("rows")),
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:8000]))
    return out


def _analyze_csv_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_csv import preview_bank_csv

    raw_snip = ""
    try:
        raw_snip = content[:12000].decode("utf-8", errors="replace")
    except Exception:
        raw_snip = ""
    if not (content or b"").strip():
        rec = {
            "source_key": f"csv:{filename}",
            "file_format": "csv",
            "kind": "checking",
            "error": "File is empty",
            "suggested_nickname": filename[:60],
            "suggested_entity_type": "personal",
            "confidence": 0.1,
            "reasons": ["This file is empty — skip it or download again"],
            "transactions_found": 0,
            "skip": True,
        }
        return [enrich_review_fields(rec, filename=filename, raw_text="")]
    try:
        prev = preview_bank_csv(content, max_rows=250)
    except Exception as e:
        rec = {
            "source_key": f"csv:{filename}",
            "file_format": "csv",
            "kind": "checking",
            "error": str(e)[:200],
            "suggested_nickname": filename[:60],
            "suggested_entity_type": "personal",
            "confidence": 0.3,
            "reasons": ["Could not parse CSV — still importable with mapping"],
            "transactions_found": 0,
        }
        return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip)]
    sample = prev.get("sample") or []
    payees = []
    dates = []
    categories: list[str] = []
    headers = [str(h) for h in (prev.get("headers") or [])]
    for row in sample[:40]:
        if isinstance(row, dict):
            payees.append(str(row.get("payee") or row.get("description") or ""))
            if row.get("txn_date") or row.get("date"):
                dates.append(str(row.get("txn_date") or row.get("date"))[:10])
            cat = row.get("category")
            if cat:
                categories.append(str(cat))
        else:
            payees.append(str(row))
    # Category + Card columns often exist but aren't in the tiny preview sample
    for m in re.finditer(r"(?i)(?:office\s*&\s*shipping|professional\s*services|restaurants?|gasoline|shopping|entertainment)", raw_snip):
        categories.append(m.group(0))
    header = _account_header_text(raw_snip)
    kind = guess_account_kind(filename=filename, raw_text=raw_snip[:4000], headers=headers)
    bank = guess_bank_label(filename, header)
    if not bank:
        for p in payees[:4]:
            if not p or re.search(r"payment to|from share|transfer", str(p), re.I):
                continue
            hit = guess_bank_label(str(p))
            if hit:
                bank = hit
                break
    if _infer_credit_union(raw_snip) and not re.search(r"loan\s+account", header, re.I):
        bank = bank or "Credit union"
        if kind in ("credit", "loan"):
            kind = "checking"
    cls = classify_account_source(
        filename=filename,
        kind=kind,
        org=bank,
        sample_payees=payees,
        sample_categories=categories,
    )
    stem = re.sub(r"\.[^.]+$", "", filename)
    stem = re.sub(r"[_\-]+", " ", stem).strip()[:50]
    last4 = _last4_from_csv_preview(headers, sample) or last4_from_text(filename, header)
    nick = _nickname(bank or "Bank", kind, last4) if (bank or last4) else (stem or _nickname(None, kind, None))
    d0 = min(dates) if dates else None
    d1 = max(dates) if dates else None
    rec = {
        "source_key": f"csv:{filename}",
        "file_format": "csv",
        "kind": kind,
        "org": bank,
        "acctid": last4,
        "suggested_nickname": nick,
        "transactions_found": prev.get("rows_scanned") or prev.get("transactions_found") or len(sample),
        "ledger_balance": prev.get("ending_balance"),
        "date_from": d0,
        "date_to": d1,
        **cls,
        "sample_payees": payees[:6],
        "sample_postings": _sample_postings(sample),
        "preview_hint": prev.get("hint"),
        "txn_fps": _fingerprints_from_csv_preview(sample),
    }
    return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip)]


def _analyze_pdf_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    raw_snip = ""
    try:
        from honestspend.services.statement_pdf import (
            extract_pdf_text,
            parse_credit_union_membership,
            parse_vehicle_finance_statement,
            preview_statement_pdf,
        )

        prev = preview_statement_pdf(content)
        try:
            raw_full, _ = extract_pdf_text(content)
            raw_snip = raw_full or ""
        except Exception:
            raw_snip = ""
    except Exception as e:
        prev = {"error": str(e)[:200], "candidates": 0}
        parse_credit_union_membership = None  # type: ignore
        parse_vehicle_finance_statement = None  # type: ignore

    if parse_vehicle_finance_statement and raw_snip:
        veh = parse_vehicle_finance_statement(raw_snip)
        if veh:
            rec = {
                "source_key": f"pdf:{filename}:auto",
                "file_format": "pdf",
                "kind": "loan",
                "org": veh.get("lender") or "Auto lender",
                "acctid": veh.get("acctid") or veh.get("last4"),
                "product": veh.get("product"),
                "loan_label": veh.get("loan_label"),
                "loan_detail": veh.get("loan_detail"),
                "brand": veh.get("loan_detail") or veh.get("lender"),
                "suggested_nickname": friendly_account_name(
                    loan_label="Car loan",
                    loan_detail=veh.get("loan_detail"),
                    last4=veh.get("last4"),
                    kind="loan",
                ),
                "transactions_found": prev.get("candidates") or 0,
                "ledger_balance": veh.get("ledger_balance"),
                "is_statement": True,
                "suggested_entity_type": "personal",
            }
            return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip[:4000])]

    if parse_credit_union_membership and raw_snip:
        shares = parse_credit_union_membership(raw_snip)
        if len(shares) >= 2:
            out: list[dict[str, Any]] = []
            for sh in shares:
                rec = {
                    "source_key": f"pdf:{filename}:share-{sh['share_id']}",
                    "file_format": "pdf",
                    "kind": sh["kind"],
                    "org": sh["org"],
                    "brand": f"Canvas {sh['friendly']}" if "canvas" in sh["org"].lower() else sh["friendly"],
                    "acctid": sh["acctid"],
                    "last4": sh["last4"],
                    "match_tails": sh["match_tails"],
                    "suggested_nickname": (
                        (f"Canvas {sh['friendly']}" if "canvas" in sh["org"].lower() else sh["friendly"])
                        + (f" · …{sh['last4']}" if sh.get("last4") else "")
                    ),
                    "transactions_found": 0,
                    "ledger_balance": sh.get("ledger_balance"),
                    "is_statement": True,
                    "apply_balance_only": True,
                    "suggested_entity_type": "personal",
                }
                out.append(enrich_review_fields(rec, filename=filename, raw_text=raw_snip[:2500]))
            return out

    kind = guess_account_kind(filename=filename, raw_text=raw_snip[:4000], product=raw_snip[:400])
    # Membership / CU header beats a card-payee named in the register
    if re.search(r"canvas\.org|canvas credit union", raw_snip[:2500], re.I):
        bank = "Canvas Credit Union"
    else:
        bank = guess_bank_label(filename, raw_snip[:2500])
    last4 = last4_from_text(filename, raw_snip[:2500])
    cls = classify_account_source(filename=filename, kind=kind, org=bank)
    stem = re.sub(r"\.[^.]+$", "", filename)
    nick = (
        _nickname(bank or "Bank", kind, last4)
        if (bank or last4)
        else (re.sub(r"[_\-]+", " ", stem).strip()[:50] or _nickname(None, kind, None))
    )
    enrich: dict[str, Any] = {}
    try:
        from honestspend.services.import_bootstrap import extract_statement_enrichment

        enrich = extract_statement_enrichment(raw_snip)
    except Exception:
        enrich = {}
    rec = {
        "source_key": f"pdf:{filename}",
        "file_format": "pdf",
        "kind": kind,
        "org": bank,
        "acctid": last4,
        "suggested_nickname": nick,
        "transactions_found": prev.get("candidates") or 0,
        "ledger_balance": prev.get("ending_balance"),
        "is_statement": True,
        "statement_fields": {k: str(v) for k, v in enrich.items() if v is not None},
        "txn_fps": _fingerprints_from_text(raw_snip),
        **cls,
        "preview_hint": prev.get("hint") or prev.get("error"),
        "sample_postings": _sample_postings(prev.get("sample")),
        "sample_payees": [
            str(r.get("payee") or "")
            for r in (prev.get("sample") or [])
            if isinstance(r, dict) and r.get("payee")
        ][:6],
    }
    out = enrich_review_fields(rec, filename=filename, raw_text=raw_snip)
    extras: list[str] = []
    if enrich.get("credit_limit") is not None:
        extras.append(f"Credit limit ${enrich['credit_limit']}")
    if enrich.get("apr_display_pct") is not None:
        extras.append(f"APR {enrich['apr_display_pct']}%")
    if enrich.get("min_payment") is not None:
        extras.append(f"Min payment ${enrich['min_payment']}")
    if enrich.get("payment_due_day") is not None:
        extras.append(f"Due day {enrich['payment_due_day']}")
    if enrich.get("statement_close_day") is not None:
        extras.append(f"Statement close day {enrich['statement_close_day']}")
    if enrich.get("promo_apr") is not None:
        extras.append(f"Promo APR {enrich.get('promo_apr_display_pct', 0)}%")
    try:
        from honestspend.services.promo_statement_parse import extract_promo_terms

        promo_rows = extract_promo_terms(raw_snip)
    except Exception:
        promo_rows = []
    if promo_rows:
        extras.append(f"{len(promo_rows)} promo/plan/offer term(s)")
        out["promo_terms"] = [
            {
                "kind": r.get("kind"),
                "name": r.get("name"),
                "principal": str(r.get("principal_remaining") or ""),
                "end": r["end_date"].isoformat() if r.get("end_date") else None,
            }
            for r in promo_rows[:12]
        ]
    if extras:
        lines = list(out.get("review_lines") or [])
        lines.append("Statement facts: " + ", ".join(extras))
        for r in (out.get("promo_terms") or [])[:6]:
            bit = r.get("name") or r.get("kind")
            if r.get("principal"):
                bit += f" ${r['principal']}"
            if r.get("end"):
                bit += f" through {r['end']}"
            lines.append("• " + bit)
        out["review_lines"] = lines
        out["what_we_will_do"] = (
            (out.get("what_we_will_do") or "We will import this statement")
            + " — and apply balance / due / APR / limit / promos we can read."
        )
    return [out]


def _analyze_qif_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_qif import parse_qif_accounts, _read_text

    text = _read_text(content)
    accounts = parse_qif_accounts(text)
    out: list[dict[str, Any]] = []
    file_bank = guess_bank_label(filename, text[:4000])
    for a in accounts:
        payees = [str(r.get("payee") or "") for r in (a.get("rows") or [])[:40]]
        postings = _sample_postings(a.get("rows"))
        kind = guess_account_kind(
            filename=filename,
            raw_text=text[:2000],
            accttype=a.get("kind"),
        ) or (a.get("kind") or "checking")
        if (a.get("kind") or "").lower() in ("credit", "ccard"):
            kind = "credit"
        name = (a.get("name") or "").strip()
        org = file_bank or _usable_org(name)
        cls = classify_account_source(
            filename=filename,
            kind=kind,
            org=org,
            sample_payees=payees,
        )
        last4 = last4_from_text(name, filename)
        # Prefer bank + type + last4 when QIF account name is generic (!Type:Bank)
        if name and name.lower() not in ("bank", "ccard", "cash", "credit card", "oth a", "oth l"):
            nick = name[:50]
            if file_bank and file_bank.lower() not in name.lower():
                nick = f"{file_bank} · {name}"[:50]
        else:
            nick = _nickname(org or "Bank", kind, last4)
        d0, d1 = _date_range_from_rows(a.get("rows"))
        rec = {
            "source_key": a.get("external_key") or f"qif:{(name or nick)}",
            "file_format": "qif",
            "acctid": a.get("acctid") or name or last4,
            "external_key": a.get("external_key"),
            "kind": kind,
            "org": org,
            "transactions_found": a.get("transactions_found"),
            "suggested_nickname": nick,
            "date_from": d0,
            "date_to": d1,
            "rows": a.get("rows"),
            **cls,
            "sample_payees": payees[:6],
            "sample_postings": postings,
            "txn_fps": _fingerprints_from_rows(a.get("rows")),
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:8000]))
    if not out:
        stem = re.sub(r"\.[^.]+$", "", filename)
        nick = re.sub(r"[_\-]+", " ", stem).strip()[:50] or "QIF account"
        cls = classify_account_source(filename=filename, kind="checking")
        rec = {
            "source_key": f"qif:{filename}",
            "file_format": "qif",
            "kind": "checking",
            "suggested_nickname": nick,
            "transactions_found": 0,
            **cls,
            "reasons": cls.get("reasons", []) + ["Empty or unreadable QIF"],
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:4000]))
    return out


def analyze_upload(filename: str, content: bytes) -> list[dict[str, Any]]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("ofx", "qfx"):
        return _analyze_ofx_bytes(content, filename)
    if ext == "qif":
        return _analyze_qif_bytes(content, filename)
    if ext in ("csv", "txt"):
        return _analyze_csv_bytes(content, filename)
    if ext == "pdf":
        return _analyze_pdf_bytes(content, filename)
    if ext in ("xlsx", "xls"):
        from honestspend.services.activity_xlsx import parse_activity_xlsx

        parsed = parse_activity_xlsx(content, filename)
        if parsed:
            product = parsed.get("product")
            org = parsed.get("org") or "American Express"
            kind = parsed.get("kind") or "credit"
            payees = list(parsed.get("sample_payees") or [])
            cats = list(parsed.get("sample_categories") or [])
            cls = classify_account_source(
                filename=filename,
                kind=kind,
                org=org,
                acctid=parsed.get("acctid"),
                sample_payees=payees,
                sample_categories=cats,
                product=product,
            )
            last4 = parsed.get("last4")
            nick = _nickname(org, kind, last4)
            if product:
                nick = f"{org} · {product}"[:80]
                if last4 and last4 not in nick:
                    nick = f"{nick} · …{last4}"[:80]
            rec = {
                "source_key": f"{ext}:{filename}",
                "file_format": ext,
                "kind": kind,
                "org": org,
                "acctid": parsed.get("acctid") or last4,
                "product": product,
                "suggested_nickname": nick,
                "transactions_found": parsed.get("transactions_found") or 0,
                "ledger_balance": parsed.get("ledger_balance"),
                "date_from": parsed.get("date_from"),
                "date_to": parsed.get("date_to"),
                "sample_payees": payees[:6],
                "sample_postings": _sample_postings(parsed.get("rows")),
                "sample_categories": cats[:8],
                **cls,
            }
            blob = " ".join(
                x for x in (product, parsed.get("acct_mask"), filename) if x
            )
            return [enrich_review_fields(rec, filename=filename, raw_text=blob)]
        return [
            {
                "source_key": f"{ext}:{filename}",
                "file_format": ext,
                "kind": "checking",
                "suggested_nickname": filename[:50],
                "suggested_entity_type": "personal",
                "confidence": 0.35,
                "reasons": ["Excel workbook — not an issuer activity export we recognize"],
                "transactions_found": 0,
            }
        ]
    return [
        {
            "source_key": f"file:{filename}",
            "file_format": ext or "unknown",
            "kind": "checking",
            "suggested_nickname": filename[:50],
            "suggested_entity_type": "personal",
            "confidence": 0.25,
            "reasons": [f"Unknown type .{ext} — may still import if supported"],
            "transactions_found": 0,
        }
    ]


_ACTIVITY_FMTS = frozenset({"csv", "txt", "ofx", "qfx", "qif", "xlsx", "xls"})


def _is_statement_acc(acc: dict[str, Any], filename: str = "") -> bool:
    """PDF / thin statement files vs activity exports (even if the bank named them 'statement')."""
    fmt = (acc.get("file_format") or "").lower()
    n = int(acc.get("transactions_found") or 0)
    # CSV / QIF / OFX / XLSX are always the live list — never a statement to attach.
    if fmt in _ACTIVITY_FMTS:
        return False
    if fmt == "pdf" and n >= 4 and _looks_like_activity_pdf(filename or "", acc):
        return False
    if fmt == "pdf" or acc.get("is_statement"):
        return True
    return bool(re.search(r"statement", filename or "", re.I))


def _kinds_block_last4(stmt: dict[str, Any], primary: dict[str, Any]) -> bool:
    """Only block last-4 attach when both sides named opposite families."""
    ka = (stmt.get("kind") or "").lower()
    kb = (primary.get("kind") or "").lower()
    if not ka or not kb:
        return False
    cash = {"checking", "savings", "cash"}
    dest = {"credit", "loan"}
    return (ka in dest and kb in cash) or (kb in dest and ka in cash)


def _looks_like_activity_pdf(filename: str, acc: dict[str, Any]) -> bool:
    blob = " ".join(
        x
        for x in (
            filename,
            acc.get("human_title") or "",
            acc.get("preview_hint") or "",
        )
        if x
    )
    return bool(
        re.search(
            r"transaction site|account details|recent transactions|download transactions",
            blob,
            re.I,
        )
    )


def _kinds_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ka = (a.get("kind") or "").lower() or "checking"
    kb = (b.get("kind") or "").lower() or "checking"
    return ka == kb


def _kinds_ok_for_attach(stmt: dict[str, Any], primary: dict[str, Any]) -> bool:
    """Same kind required; do not assume blank kind is checking."""
    ka = (stmt.get("kind") or "").lower()
    kb = (primary.get("kind") or "").lower()
    if not ka or not kb:
        return False
    cash = {"checking", "savings", "cash"}
    dest = {"credit", "loan"}
    if (ka in dest and kb in cash) or (kb in dest and ka in cash):
        return False
    return ka == kb


def _banks_ok_for_attach(stmt: dict[str, Any], primary: dict[str, Any]) -> bool:
    """When both sides named a bank, they must match. Missing label is allowed."""
    ba = (stmt.get("brand") or stmt.get("bank_label") or stmt.get("org") or "").strip()
    bb = (primary.get("brand") or primary.get("bank_label") or primary.get("org") or "").strip()
    if not ba or not bb:
        return True
    return _banks_compatible(stmt, primary)


def _account_header_text(raw: str, *, max_lines: int = 16) -> str:
    """Identity lines only — stop at the first dated register row."""
    lines: list[str] = []
    for line in (raw or "").splitlines():
        head = line[:80]
        if (
            re.match(r"^\s*[\d\"]", line)
            and re.search(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}", head)
            and not re.search(r"activity|statement\s+period|prepared|start date|end date", line, re.I)
        ):
            break
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def _last4_from_csv_preview(headers: list[str], sample: list[Any]) -> str | None:
    idx = None
    for i, h in enumerate(headers or []):
        if re.search(r"card\s*(no\.?|num|number|#)|last\s*4", str(h), re.I):
            idx = i
            break
    if idx is None:
        return None
    tails: list[str] = []
    for row in sample or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("raw")
        if not isinstance(raw, list) or idx >= len(raw):
            continue
        digits = re.sub(r"\D", "", str(raw[idx] or ""))
        if len(digits) >= 4:
            tails.append(digits[-4:])
    if not tails:
        return None
    return max(set(tails), key=tails.count)


_BRAND_FAMILIES = (
    frozenset({"american express", "amex", "blue business plus", "blue business cash", "amazon business prime", "hilton honors"}),
    frozenset({"citi", "citibank", "home depot"}),
    frozenset({"td bank", "target"}),
    frozenset({"chase", "prime visa", "chase freedom", "chase sapphire", "chase ink"}),
    frozenset({"wells fargo", "wells fargo reflect"}),
    frozenset({"capital one"}),
    frozenset({"discover"}),
    frozenset({"canvas credit union", "credit union"}),
    frozenset({"scheels", "fnbo", "first national bank of omaha"}),
)


def _banks_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ba = (a.get("brand") or a.get("bank_label") or a.get("org") or "").strip().lower()
    bb = (b.get("brand") or b.get("bank_label") or b.get("org") or "").strip().lower()
    if not ba or not bb:
        return False
    if ba == bb or ba in bb or bb in ba:
        return True
    for fam in _BRAND_FAMILIES:
        if any(x in ba for x in fam) and any(x in bb for x in fam):
            return True
    return False


_AMT_TOKEN = re.compile(r"(-?\$\s*-?\s*[\d,]+\.\d{2}|-?[\d,]+\.\d{2})")


def _txn_fingerprint(date_s: str | None, amount: Any, payee: str = "") -> str | None:
    """Date + absolute cents + short payee. Sign-insensitive for card exports."""
    if not date_s:
        return None
    d = str(date_s)[:10]
    if re.match(r"\d{1,2}/\d{1,2}/\d{2,4}$", d):
        try:
            from datetime import datetime as dt

            d = dt.strptime(d, "%m/%d/%Y" if len(d) > 8 else "%m/%d/%y").date().isoformat()
        except ValueError:
            return None
    try:
        from decimal import Decimal as D

        raw_amt = re.sub(r"[^\d.\-]", "", str(amount))
        cents = abs(int((D(raw_amt) * 100).to_integral_value()))
    except Exception:
        return None
    pay = re.sub(r"[^a-z0-9]+", "", (payee or "").lower())[:10]
    return f"{d}|{cents}|{pay}"


def _fp_cores(acc: dict[str, Any]) -> set[str]:
    """Date|cents only — PDF payee text is often truncated vs the CSV."""
    keys: set[str] = set()
    for x in acc.get("txn_fps") or []:
        parts = str(x).split("|")
        if len(parts) >= 2 and parts[0] and parts[1]:
            keys.add(f"{parts[0]}|{parts[1]}")
    return keys


def _statement_year(text: str) -> str | None:
    m = re.search(
        r"statement\s+period\s+\d{1,2}/\d{1,2}/(\d{2,4})",
        text or "",
        re.I,
    )
    if m:
        y = m.group(1)
        return y if len(y) == 4 else f"20{y}"
    m = re.search(r"\b(20\d{2})\s+totals\s+year-to-date\b", text or "", re.I)
    if m:
        return m.group(1)
    return None


def _fingerprints_from_text(text: str) -> list[str]:
    """Pull date+amount pairs even when Discover puts the amount a few lines later."""
    if not text:
        return []
    year = _statement_year(text)
    body = text
    cut = re.search(r"\n\s*Transactions\b", text, re.I)
    if cut:
        body = text[cut.start() :]
    lines = body.splitlines()
    date_re = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{1,2})\b")
    out: list[str] = []
    for i, line in enumerate(lines):
        if re.search(
            r"total payments for this period|interest charge calculation|"
            r"totals year-to-date|promotional rate expires",
            line,
            re.I,
        ):
            break
        ds = date_re.findall(line)
        if not ds:
            continue
        raw_date = ds[0]
        if re.fullmatch(r"\d{1,2}/\d{1,2}", raw_date):
            if not year:
                continue
            raw_date = f"{raw_date}/{year}"
        amts = _AMT_TOKEN.findall(line)
        if not amts:
            for j in range(i + 1, min(i + 5, len(lines))):
                if date_re.search(lines[j]):
                    break
                amts = _AMT_TOKEN.findall(lines[j])
                if amts:
                    break
        if not amts:
            continue
        if re.sub(r"[^\d]", "", amts[-1]) in ("000", "0"):
            continue
        payee = date_re.sub(" ", line)
        fp = _txn_fingerprint(raw_date, amts[-1], payee)
        if fp:
            out.append(fp)
    return out


def _ordered_date_amounts(acc: dict[str, Any]) -> list[str]:
    """Document-order date|cents pairs (first line items first)."""
    keys: list[str] = []
    seen: set[str] = set()
    for x in acc.get("txn_fps") or []:
        parts = str(x).split("|")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        key = f"{parts[0]}|{parts[1]}"
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _head_line_item_hits(probe: dict[str, Any], pool: dict[str, Any], *, head: int = 4) -> int:
    """How many of probe's first date→amount line items appear in pool."""
    lead = _ordered_date_amounts(probe)[:head]
    if not lead:
        return 0
    target = set(_ordered_date_amounts(pool))
    return sum(1 for k in lead if k in target)


def _fingerprints_from_rows(rows: list[Any] | None) -> list[str]:
    out: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        d = row.get("date") or row.get("txn_date")
        if hasattr(d, "isoformat"):
            d = d.isoformat()[:10]
        fp = _txn_fingerprint(
            d,
            row.get("amount"),
            str(row.get("payee") or row.get("description") or ""),
        )
        if fp:
            out.append(fp)
    return out


def _fingerprints_from_csv_preview(sample: list[Any]) -> list[str]:
    return _fingerprints_from_rows(sample)


def _primary_keys(acc: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    if acc.get("last4"):
        keys.add(str(acc["last4"]))
    for t in acc.get("match_tails") or []:
        if t:
            keys.add(str(t))
    return keys


def _primary_rank(acc: dict[str, Any]) -> tuple[int, int, int]:
    fmt = (acc.get("file_format") or "").lower()
    order = {"ofx": 0, "qfx": 0, "csv": 1, "xlsx": 2, "xls": 2, "qif": 3, "pdf": 5}
    stmt = 1 if acc.get("is_statement") or fmt == "pdf" else 0
    return (stmt, order.get(fmt, 4), -int(acc.get("transactions_found") or 0))


def cluster_batch_sources(sources: list[dict[str, Any]]) -> None:
    """Attach statements to the matching activity file (first line items, then last-4).

    Mutates account dicts: action=attach, attach_to_*, what_we_will_do, attachments.
    Does not merge two live accounts that only share a member number.
    """
    refs: list[dict[str, Any]] = []
    for src in sources:
        for acc in src.get("accounts") or []:
            refs.append({"fi": int(src.get("file_index") or 0), "fname": src.get("filename") or "", "acc": acc})
    if len(refs) < 2:
        return

    primaries = [i for i, r in enumerate(refs) if not _is_statement_acc(r["acc"], r["fname"])]
    statements = [i for i, r in enumerate(refs) if _is_statement_acc(r["acc"], r["fname"])]
    if not statements and len(primaries) < 2:
        return

    parent = list(range(len(refs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # Duplicate activity files (same last-4, compatible bank + kind)
    by_l4: dict[str, list[int]] = {}
    for i in primaries:
        l4 = refs[i]["acc"].get("last4")
        if l4:
            by_l4.setdefault(str(l4), []).append(i)
    for idxs in by_l4.values():
        for j in idxs[1:]:
            if _banks_compatible(refs[idxs[0]]["acc"], refs[j]["acc"]) and _kinds_compatible(
                refs[idxs[0]]["acc"], refs[j]["acc"]
            ):
                union(idxs[0], j)

    def _copy_last4(si: int, pi: int) -> None:
        lead = refs[pi]["acc"]
        stmt = refs[si]["acc"]
        if stmt.get("last4") and not lead.get("last4"):
            lead["last4"] = stmt["last4"]
            lead["suggested_nickname"] = friendly_account_name(
                brand=lead.get("brand") or stmt.get("brand") or lead.get("bank_label"),
                bank=lead.get("bank_label") or stmt.get("bank_label"),
                kind=lead.get("kind") or stmt.get("kind") or "credit",
                last4=stmt["last4"],
            )
            lead["human_title"] = lead["suggested_nickname"]

    # First 3–4 statement line items (date → amount) against each activity file
    for si in statements:
        stmt = refs[si]["acc"]
        head = _ordered_date_amounts(stmt)[:4]
        need = 2 if len(head) >= 2 else (1 if len(head) == 1 else 0)
        if need == 0:
            continue
        hit_roots: list[int] = []
        best = 0
        for i in primaries:
            pri = refs[i]["acc"]
            if not _kinds_ok_for_attach(stmt, pri):
                continue
            hits = _head_line_item_hits(stmt, pri, head=4)
            local_need = need
            if len(head) >= 4 and len(_ordered_date_amounts(pri)) >= 8:
                local_need = max(need, 3)
            if hits < local_need:
                continue
            root = find(i)
            if hits > best:
                best = hits
                hit_roots = [root]
            elif hits == best and root not in hit_roots:
                hit_roots.append(root)
        if len(hit_roots) == 1:
            union(si, hit_roots[0])
            _copy_last4(si, hit_roots[0])

    # Broader fingerprint overlap if the head pass did not unique-match
    for si in statements:
        if find(si) != si:
            continue
        sfps = _fp_cores(refs[si]["acc"])
        if len(sfps) < 2:
            continue
        hit_roots = []
        for i in primaries:
            overlap = sfps & _fp_cores(refs[i]["acc"])
            if len(overlap) < 2:
                continue
            if not _kinds_ok_for_attach(refs[si]["acc"], refs[i]["acc"]):
                continue
            if not _banks_compatible(refs[si]["acc"], refs[i]["acc"]):
                continue
            root = find(i)
            if root not in hit_roots:
                hit_roots.append(root)
        if len(hit_roots) == 1:
            union(si, hit_roots[0])
            _copy_last4(si, hit_roots[0])

    # Last-4 backup — prefer the account's own last-4, not a shared member number
    for si in statements:
        if find(si) != si:
            continue
        sl4 = _last4_norm(refs[si]["acc"].get("last4"))
        if not sl4:
            continue
        own: list[int] = []
        via_tail: list[int] = []
        for i in primaries:
            acc = refs[i]["acc"]
            root = find(i)
            if _same_last4(acc.get("last4"), sl4):
                if root not in own:
                    own.append(root)
            elif sl4 in {_last4_norm(t) for t in (acc.get("match_tails") or []) if t}:
                if root not in via_tail:
                    via_tail.append(root)
        if len(own) == 1:
            # Unique last-4 wins even when kind was guessed wrong (card
            # activity CSV often looks like checking). Only refuse when both
            # sides named incompatible banks (Amex …1009 vs Scheels …1009).
            oi = own[0]
            if _banks_ok_for_attach(refs[si]["acc"], refs[oi]["acc"]):
                union(si, oi)
        elif len(own) > 1:
            exact = [
                i
                for i in own
                if _kinds_compatible(refs[si]["acc"], refs[i]["acc"])
                and _banks_ok_for_attach(refs[si]["acc"], refs[i]["acc"])
            ]
            if len(exact) == 1:
                union(si, exact[0])
            else:
                kind_hits = [
                    i
                    for i in own
                    if not _kinds_block_last4(refs[si]["acc"], refs[i]["acc"])
                    and _banks_ok_for_attach(refs[si]["acc"], refs[i]["acc"])
                ]
                if len(kind_hits) == 1:
                    union(si, kind_hits[0])
        elif len(via_tail) == 1:
            ti = via_tail[0]
            if not _kinds_block_last4(refs[si]["acc"], refs[ti]["acc"]) and _banks_ok_for_attach(
                refs[si]["acc"], refs[ti]["acc"]
            ):
                union(si, ti)

    groups: dict[int, list[int]] = {}
    for i in range(len(refs)):
        groups.setdefault(find(i), []).append(i)

    for members in groups.values():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda i: _primary_rank(refs[i]["acc"]))
        lead = ranked[0]
        lead_acc = refs[lead]["acc"]
        lead_name = lead_acc.get("human_title") or lead_acc.get("suggested_nickname") or "account"
        attached: list[dict[str, Any]] = []
        for i in ranked[1:]:
            acc = refs[i]["acc"]
            acc["action"] = "attach"
            acc["attach_to_file_index"] = refs[lead]["fi"]
            acc["attach_to_source_key"] = lead_acc.get("source_key")
            acc["what_we_will_do"] = (
                f"Attach to {lead_name} — apply statement balance, due date, APR/limit/promos"
            )
            attached.append(
                {
                    "filename": refs[i]["fname"],
                    "source_key": acc.get("source_key"),
                    "file_index": refs[i]["fi"],
                    "ledger_balance": acc.get("ledger_balance"),
                    "statement_fields": acc.get("statement_fields") or {},
                }
            )
        if attached:
            lead_acc["attachments"] = (lead_acc.get("attachments") or []) + attached
            extra = f" plus {len(attached)} statement(s) for forecasting"
            lead_acc["what_we_will_do"] = (lead_acc.get("what_we_will_do") or f"Create {lead_name}") + extra
            # Statement branding / business name is usually better than the activity file
            for i in ranked[1:]:
                att = refs[i]["acc"]
                if att.get("owner_name") and not lead_acc.get("owner_name"):
                    lead_acc["owner_name"] = att["owner_name"]
                    lead_acc["suggested_entity_type"] = "business"
                if att.get("apply_balance_only") and att.get("suggested_nickname"):
                    lead_acc["brand"] = att.get("brand") or lead_acc.get("brand")
                    lead_acc["suggested_nickname"] = att["suggested_nickname"]
                    lead_acc["human_title"] = att.get("human_title") or att["suggested_nickname"]
                    continue
                att_brand = att.get("brand") or att.get("bank_label")
                lead_brand = lead_acc.get("brand") or lead_acc.get("bank_label") or ""
                if att_brand and (
                    not lead_brand
                    or lead_brand.lower() in _GENERIC_LABELS
                ) and att_brand.lower() not in _GENERIC_LABELS:
                    lead_acc["brand"] = att_brand
                    lead_acc["bank_label"] = att_brand
                    lead_acc["suggested_nickname"] = friendly_account_name(
                        brand=att_brand,
                        product=att.get("product") or lead_acc.get("product"),
                        loan_label=lead_acc.get("loan_label") or att.get("loan_label"),
                        loan_detail=None,
                        bank=att_brand,
                        kind=lead_acc.get("kind") or "checking",
                        last4=lead_acc.get("last4") or att.get("last4"),
                    )
                    lead_acc["human_title"] = lead_acc["suggested_nickname"]


def assign_plan_entities(session: Session, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Personal plus each named business printed on the files."""
    buckets: dict[str, dict[str, Any]] = {}

    def _ensure(key: str, et: str, display: str, conf: float) -> None:
        if key not in buckets:
            match = _match_profile(session, et, display if et == "business" else None)
            buckets[key] = {
                "key": key,
                "entity_type": et,
                "display_name": match.display_name if match else display,
                "action": "use_existing" if match else "create",
                "profile_id": match.id if match else None,
                "confidence": conf,
            }
        else:
            buckets[key]["confidence"] = max(float(buckets[key].get("confidence") or 0), conf)

    personal = _match_profile(session, "personal")
    _ensure(
        "personal",
        "personal",
        personal.display_name if personal else "Personal",
        0.9,
    )

    named_owners: list[str] = []
    for src in sources:
        for acc in src.get("accounts") or []:
            owner = (acc.get("owner_name") or "").strip()
            if owner:
                key = _owner_key(owner)
                if key not in { _owner_key(n) for n in named_owners }:
                    named_owners.append(owner)
                    _ensure(key, "business", _title_biz(owner), 0.8)

    for src in sources:
        for acc in src.get("accounts") or []:
            owner = (acc.get("owner_name") or "").strip()
            et = acc.get("suggested_entity_type") or "personal"
            if owner:
                key = _owner_key(owner)
                display = _title_biz(owner)
                et = "business"
            elif et == "business" and named_owners:
                # Don't invent a third unnamed "Business" — user picks which company
                key = "personal"
                display = "Personal"
                acc["needs_owner"] = True
                et = "personal"
                lines = list(acc.get("review_lines") or [])
                lines.append("Looks like business money — pick which company above, or add one")
                acc["review_lines"] = lines
            elif et == "business":
                key = "business"
                display = "Business"
            else:
                key = "personal"
                display = "Personal"
            acc["entity_key"] = key
            acc["suggested_entity_type"] = et
            _ensure(key, et if key != "personal" else "personal", display, float(acc.get("confidence") or 0.5))
            nick = acc.get("suggested_nickname") or acc.get("human_title") or "account"
            if acc.get("action") == "attach":
                continue
            if acc.get("action") == "match" and acc.get("matched_nickname"):
                acc["what_we_will_do"] = f'We will match existing “{acc.get("matched_nickname")}”'
            else:
                acc["what_we_will_do"] = f'We will create “{nick}” under {display}'
            if acc.get("profile_id") is None:
                acc["profile_id"] = buckets[key].get("profile_id")

    _inherit_attach_entity_from_target(sources)

    # Stable order: Personal, named businesses, generic Business last
    ordered: list[dict[str, Any]] = []
    if "personal" in buckets:
        ordered.append(buckets.pop("personal"))
    generic = buckets.pop("business", None)
    named = [v for k, v in buckets.items() if k.startswith("business:")]
    named.sort(key=lambda e: (e.get("display_name") or "").lower())
    ordered.extend(named)
    if generic:
        ordered.append(generic)
    return ordered


def _inherit_attach_entity_from_target(sources: list[dict[str, Any]]) -> None:
    """Attached files adopt the book/type of the account they fold into."""
    index: dict[tuple[Any, Any], dict[str, Any]] = {}
    for src in sources:
        fi = src.get("file_index")
        for acc in src.get("accounts") or []:
            index[(fi, acc.get("source_key"))] = acc
    for acc in index.values():
        if (acc.get("action") or "") != "attach":
            continue
        tgt = index.get((acc.get("attach_to_file_index"), acc.get("attach_to_source_key")))
        if not tgt:
            continue
        for key in ("entity_key", "suggested_entity_type", "kind"):
            if tgt.get(key) not in (None, "", "—"):
                acc[key] = tgt[key]
        acc.pop("profile_id", None)
        if tgt.get("profile_id") not in (None, "", "—"):
            acc["profile_id"] = tgt["profile_id"]


def build_smart_plan(
    session: Session,
    files: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Build entity + account mapping plan for a batch of bank downloads."""
    existing_profiles = [
        {
            "id": p.id,
            "slug": p.slug,
            "display_name": p.display_name,
            "entity_type": p.entity_type,
        }
        for p in session.query(Profile).order_by(Profile.id.asc()).all()
    ]
    existing_accounts = [
        {
            "id": a.id,
            "nickname": a.nickname,
            "kind": a.kind,
            "profile_id": a.profile_id,
            "external_id": a.external_id,
            "institution": a.institution,
        }
        for a in session.query(Account)
        .filter(Account.archived_at.is_(None))
        .order_by(Account.id.asc())
        .all()
    ]

    sources: list[dict[str, Any]] = []

    for idx, (fname, content) in enumerate(files):
        accounts = analyze_upload(fname, content)
        file_entry: dict[str, Any] = {
            "file_index": idx,
            "filename": fname,
            "size_bytes": len(content),
            "accounts": [],
        }
        for acc in accounts:
            ext = acc.get("external_key")
            matched = _match_account(
                session,
                external_key=ext,
                acctid=acc.get("acctid") or acc.get("last4"),
                nickname_hint=acc.get("suggested_nickname") or acc.get("human_title") or "",
                kind=acc.get("kind"),
                bank_label=acc.get("bank_label") or acc.get("institution"),
                loan_label=acc.get("loan_label"),
                loan_detail=acc.get("loan_detail"),
                last4=acc.get("last4"),
            )
            clean = {k: v for k, v in acc.items() if k != "rows"}
            clean.update(
                {
                    "action": "match" if matched else "create",
                    "account_id": matched.id if matched else None,
                    "matched_nickname": matched.nickname if matched else None,
                    "profile_id": matched.profile_id if matched else None,
                }
            )
            file_entry["accounts"].append(clean)
        sources.append(file_entry)

    cluster_batch_sources(sources)
    entities = assign_plan_entities(session, sources)

    n_acct = sum(
        1
        for s in sources
        for a in (s.get("accounts") or [])
        if (a.get("action") or "create") != "attach"
    )
    n_stmt = sum(
        1
        for s in sources
        for a in (s.get("accounts") or [])
        if (a.get("action") or "") == "attach" or a.get("is_statement")
    )
    n_biz = sum(1 for e in entities if e.get("entity_type") == "business")
    n_with_bank = sum(
        1
        for s in sources
        for a in (s.get("accounts") or [])
        if a.get("bank_label") or a.get("last4")
    )
    labels = []
    for s in sources:
        for a in s.get("accounts") or []:
            t = a.get("human_title") or a.get("suggested_nickname")
            if t and t not in labels:
                labels.append(t)
    label_preview = ", ".join(labels[:4])
    if len(labels) > 4:
        label_preview += f" (+{len(labels) - 4} more)"

    return {
        "ok": True,
        "file_count": len(files),
        "account_source_count": n_acct,
        "entities": entities,
        "existing_profiles": existing_profiles,
        "existing_accounts": existing_accounts,
        "sources": sources,
        "ready_for_one_tap": True,
        "summary": (
            f"We reviewed {len(files)} file(s) and found {n_acct} account(s)"
            + (f" · {n_stmt} statement(s) to apply" if n_stmt else "")
            + (f" · {n_with_bank} with bank / last-4 labels" if n_with_bank else "")
            + (f". {label_preview}" if label_preview else "")
            + ". Confirm names below — or just import."
        ),
        "hint": (
            "Each numbered card is an account. Statements we matched stay on that card — "
            "if the match is wrong, or we could not pair a statement, pick a different "
            "number from the list. That applies New Balance, due date, APR, limit, and promos."
        ),
    }


def _ensure_entities(session: Session, entities: list[dict[str, Any]]) -> dict[str, int]:
    """Return map entity_key -> profile_id, creating as needed."""
    key_to_id: dict[str, int] = {}
    for ent in entities:
        key = ent.get("key") or "personal"
        action = (ent.get("action") or "create").lower()
        pid = ent.get("profile_id")
        if action == "use_existing" and pid:
            key_to_id[key] = int(pid)
            continue
        if pid and action != "create":
            key_to_id[key] = int(pid)
            continue
        et = normalize_entity_type(ent.get("entity_type") or "personal")
        # Reuse existing of same type if create not forced and one exists
        if action != "create":
            existing = _match_profile(session, et, ent.get("display_name"))
            if existing:
                key_to_id[key] = int(existing.id)
                continue
        name = (ent.get("display_name") or ("Personal" if et == "personal" else "Business")).strip()
        slug = unique_slug(session, name if et == "business" else et)
        from honestspend.services.profiles import DEFAULT_TAX_FORM

        p = Profile(
            slug=slug,
            display_name=name[:128],
            entity_type=et,
            tax_form_primary=DEFAULT_TAX_FORM.get(et, "1040"),
        )
        session.add(p)
        session.flush()
        try:
            apply_coa_for_profile(session, p)
        except Exception:
            pass
        key_to_id[key] = int(p.id)
    return key_to_id


def _profile_id_for_decision(
    dec: dict[str, Any],
    key_to_profile: dict[str, int],
) -> int:
    """Book the user picked wins over a leftover analyze profile_id."""
    ekey = dec.get("entity_key") or "personal"
    if ekey == "individual":
        ekey = "personal"
    if ekey in key_to_profile:
        return int(key_to_profile[ekey])
    if str(ekey).startswith("business"):
        for k, pid in key_to_profile.items():
            if str(k).startswith("business"):
                return int(pid)
    raw = dec.get("profile_id")
    if raw not in (None, "", "—"):
        return int(raw)
    return int(next(iter(key_to_profile.values())))


def commit_smart_plan(
    session: Session,
    *,
    plan: dict[str, Any],
    files: list[tuple[str, bytes]],
    auto_categorize: bool = True,
    amount_sign: str = "bank",
) -> dict[str, Any]:
    """Execute user-confirmed (or auto) plan against uploaded file bytes."""
    entities = plan.get("entities") or []
    sources = plan.get("sources") or []
    key_to_profile = _ensure_entities(session, entities)

    results: list[dict[str, Any]] = []
    total_created = 0

    # Index plan account decisions by (file_index, source_key)
    decisions: dict[tuple[int, str], dict[str, Any]] = {}
    for src in sources:
        fi = int(src.get("file_index") or 0)
        for acc in src.get("accounts") or []:
            sk = acc.get("source_key") or ""
            decisions[(fi, sk)] = acc

    created_ids: dict[tuple[int, str], int] = {}
    work: list[tuple[int, str, bytes]] = [(i, fn, blob) for i, (fn, blob) in enumerate(files)]

    def _is_attach_file(idx: int, fname: str) -> bool:
        ext = (fname.rsplit(".", 1)[-1] if "." in fname else "").lower()
        if ext != "pdf":
            return False
        for (fi, sk), dec in decisions.items():
            if fi == idx and (dec.get("action") or "").lower() == "attach":
                return True
        return False

    work.sort(key=lambda it: (1 if _is_attach_file(it[0], it[1]) else 0, it[0]))

    for idx, fname, content in work:
        ext = (fname.rsplit(".", 1)[-1] if "." in fname else "").lower()
        analyzed = analyze_upload(fname, content)

        if ext in ("ofx", "qfx"):
            # Prefer multi import with explicit account map built from decisions
            from honestspend.services.bank_ofx import import_ofx_multi, parse_ofx_accounts
            from honestspend.services.bank_ofx import _read_text

            text = _read_text(content)
            stmts = parse_ofx_accounts(text)
            # Ensure accounts exist per decision
            account_map: dict[str, int] = {}
            for stmt in stmts:
                sk = stmt.get("external_key") or f"ofx:{(stmt.get('acctid') or '')}"
                dec = decisions.get((idx, sk)) or {}
                # also try matching analyzed keys
                if not dec:
                    for a in analyzed:
                        if a.get("acctid") == stmt.get("acctid"):
                            dec = decisions.get((idx, a.get("source_key") or "")) or a
                            break
                ekey = dec.get("entity_key") or "personal"
                action = (dec.get("action") or "create").lower()
                if action == "skip":
                    continue
                profile_id = _profile_id_for_decision(dec, key_to_profile)
                acct_id = dec.get("account_id")
                if action == "match" and acct_id:
                    account_map[stmt.get("acctid") or sk] = int(acct_id)
                    if sk:
                        account_map[sk] = int(acct_id)
                    continue
                # create
                existing = _match_account(
                    session,
                    external_key=stmt.get("external_key"),
                    acctid=stmt.get("acctid"),
                    nickname_hint=dec.get("suggested_nickname") or "",
                )
                if existing and action != "create":
                    account_map[stmt.get("acctid") or sk] = int(existing.id)
                    continue
                kind = dec.get("kind") or stmt.get("kind") or "checking"
                nick = (dec.get("suggested_nickname") or _nickname(stmt.get("org"), kind, stmt.get("acctid")))[:80]
                acct = Account(
                    profile_id=profile_id,
                    kind=kind if kind in ("checking", "savings", "credit", "cash", "loan") else "checking",
                    nickname=nick,
                    institution=(stmt.get("org") or None),
                    current_balance=Decimal("0"),
                    external_id=stmt.get("external_key"),
                    is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                )
                session.add(acct)
                session.flush()
                account_map[stmt.get("acctid") or sk] = int(acct.id)
                if sk:
                    account_map[sk] = int(acct.id)

            for a in analyzed:
                sk0 = a.get("source_key") or ""
                aid0 = account_map.get(a.get("acctid") or "") or account_map.get(sk0)
                if aid0:
                    created_ids[(idx, sk0)] = int(aid0)

            multi_res = import_ofx_multi(
                session,
                file_obj=content,
                filename=fname,
                auto_categorize=auto_categorize,
                amount_sign=amount_sign,
                auto_create_accounts=False,
                account_map=account_map,
            )
            total_created += int(multi_res.get("transactions_created") or 0)
            results.append({"filename": fname, "format": "ofx", **multi_res})
            continue

        if ext == "qif":
            from honestspend.services.bank_qif import import_qif_multi, parse_qif_accounts, _read_text as _qif_read

            stmts = parse_qif_accounts(_qif_read(content))
            account_map: dict[str, int] = {}
            for stmt in stmts:
                sk = stmt.get("external_key") or f"qif:{(stmt.get('name') or '')}"
                dec = decisions.get((idx, sk)) or {}
                if not dec:
                    for a in analyzed:
                        if a.get("source_key") == sk or a.get("acctid") == stmt.get("name"):
                            dec = decisions.get((idx, a.get("source_key") or "")) or a
                            break
                ekey = dec.get("entity_key") or "personal"
                action = (dec.get("action") or "create").lower()
                if action == "skip":
                    continue
                profile_id = _profile_id_for_decision(dec, key_to_profile)
                acct_id = dec.get("account_id")
                name = stmt.get("name") or sk
                if action == "match" and acct_id:
                    account_map[name] = int(acct_id)
                    account_map[sk] = int(acct_id)
                    continue
                existing = _match_account(
                    session,
                    external_key=stmt.get("external_key"),
                    acctid=None,
                    nickname_hint=dec.get("suggested_nickname") or name,
                )
                if existing and action != "create":
                    account_map[name] = int(existing.id)
                    account_map[sk] = int(existing.id)
                    continue
                kind = dec.get("kind") or stmt.get("kind") or "checking"
                nick = (dec.get("suggested_nickname") or name)[:80]
                l4 = dec.get("last4")
                if l4 and str(l4) not in nick:
                    nick = f"{nick} · …{l4}"[:80]
                acct = Account(
                    profile_id=profile_id,
                    kind=kind if kind in ("checking", "savings", "credit", "cash", "loan") else "checking",
                    nickname=nick,
                    current_balance=Decimal("0"),
                    external_id=stmt.get("external_key"),
                    is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                )
                session.add(acct)
                session.flush()
                account_map[name] = int(acct.id)
                account_map[sk] = int(acct.id)

            for a in analyzed:
                sk0 = a.get("source_key") or ""
                aid0 = account_map.get(a.get("acctid") or "") or account_map.get(sk0)
                if not aid0 and a.get("acctid"):
                    aid0 = account_map.get(str(a.get("acctid")).lower())
                if aid0:
                    created_ids[(idx, sk0)] = int(aid0)

            multi_res = import_qif_multi(
                session,
                file_obj=content,
                filename=fname,
                auto_categorize=auto_categorize,
                amount_sign=amount_sign,
                auto_create_accounts=False,
                account_map=account_map,
            )
            total_created += int(multi_res.get("transactions_created") or 0)
            results.append({"filename": fname, "format": "qif", **multi_res})
            continue

        # CSV / PDF / xlsx / xls — single account source per file typically
        for acc in analyzed:
            sk = acc.get("source_key") or f"file:{fname}"
            dec = decisions.get((idx, sk)) or acc
            if acc.get("skip") or dec.get("skip"):
                results.append(
                    {
                        "filename": fname,
                        "format": ext,
                        "skipped": True,
                        "error": acc.get("error") or "Empty or unreadable file",
                    }
                )
                continue
            ekey = dec.get("entity_key") or "personal"
            action = (dec.get("action") or "create").lower()
            if action == "skip":
                results.append(
                    {
                        "filename": fname,
                        "format": ext,
                        "skipped": True,
                        "error": "Skipped — you chose not to import this account",
                    }
                )
                continue
            profile_id = _profile_id_for_decision(dec, key_to_profile)
            acct_id = dec.get("account_id")
            if action == "attach":
                afi = int(dec.get("attach_to_file_index") or -1)
                ask = str(dec.get("attach_to_source_key") or "")
                if (afi, ask) not in created_ids:
                    # last-ditch: unique source_key match regardless of file index
                    hits = [cid for (cfi, csk), cid in created_ids.items() if csk == ask]
                    if len(hits) == 1:
                        created_ids[(afi, ask)] = hits[0]
                if (afi, ask) in created_ids:
                    target_id = created_ids[(afi, ask)]
                    created_ids[(idx, sk)] = target_id
                    # fall through to import onto the matched account
                else:
                    action = "create"
            if action == "attach":
                pass
            elif action == "match" and acct_id:
                target_id = int(acct_id)
            else:
                existing = _match_account(
                    session,
                    external_key=None,
                    acctid=None,
                    nickname_hint=dec.get("suggested_nickname") or "",
                )
                if existing and action != "create":
                    target_id = int(existing.id)
                else:
                    kind = dec.get("kind") or acc.get("kind") or "checking"
                    nick = (dec.get("suggested_nickname") or fname)[:80]
                    inst = dec.get("bank_label") or dec.get("org") or acc.get("bank_label") or acc.get("org")
                    acct = Account(
                        profile_id=profile_id,
                        kind=kind if kind in ("checking", "savings", "credit", "cash", "loan") else "checking",
                        nickname=nick,
                        institution=_usable_org(str(inst) if inst else None),
                        current_balance=Decimal("0"),
                        is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                    )
                    session.add(acct)
                    session.flush()
                    target_id = int(acct.id)

            created_ids[(idx, sk)] = target_id

            if ext in ("csv", "txt"):
                from honestspend.services.bank_csv import import_bank_csv

                res = import_bank_csv(
                    session,
                    account_id=target_id,
                    file_obj=content,
                    filename=fname,
                    auto_categorize=auto_categorize,
                    amount_sign=amount_sign,
                )
                created = getattr(res, "transactions_created", 0) or 0
                total_created += created
                results.append(
                    {
                        "filename": fname,
                        "format": "csv",
                        "account_id": target_id,
                        "transactions_created": created,
                        "transactions_found": getattr(res, "rows_scanned", 0),
                        "skipped_existing": getattr(res, "skipped_existing", 0),
                        "errors": getattr(res, "errors", [])[:5],
                    }
                )
            elif ext == "pdf" and (dec.get("apply_balance_only") or acc.get("apply_balance_only")):
                bal = dec.get("ledger_balance") or acc.get("ledger_balance")
                if bal:
                    try:
                        from decimal import Decimal as D
                        from honestspend.services.reconcile import set_institution_balance

                        set_institution_balance(
                            session, target_id, D(str(bal)), mark_reconciled=False
                        )
                    except Exception:
                        pass
                results.append(
                    {
                        "filename": fname,
                        "format": "pdf",
                        "account_id": target_id,
                        "transactions_created": 0,
                        "applied_balance": bal,
                    }
                )
            elif ext == "pdf":
                try:
                    from honestspend.services.statement_pdf import import_statement_pdf

                    res = import_statement_pdf(
                        session,
                        account_id=target_id,
                        file_obj=content,
                        filename=fname,
                        auto_categorize=auto_categorize,
                        amount_sign=amount_sign,
                    )
                    created = getattr(res, "transactions_created", 0) or res.get("transactions_created", 0) if isinstance(res, dict) else 0
                    if not isinstance(created, int):
                        created = 0
                    total_created += created
                    results.append(
                        {
                            "filename": fname,
                            "format": "pdf",
                            "account_id": target_id,
                            "transactions_created": created,
                            "result": res if isinstance(res, dict) else str(type(res)),
                        }
                    )
                except Exception as e:
                    results.append({"filename": fname, "format": "pdf", "error": str(e)[:300]})
            elif ext in ("xlsx", "xls"):
                try:
                    from honestspend.services.activity_xlsx import (
                        import_activity_xlsx,
                        parse_activity_xlsx,
                    )

                    if parse_activity_xlsx(content, fname):
                        res = import_activity_xlsx(
                            session,
                            account_id=target_id,
                            content=content,
                            filename=fname,
                        )
                        created = getattr(res, "transactions_created", 0) or 0
                        total_created += created
                        results.append(
                            {
                                "filename": fname,
                                "format": "xlsx",
                                "account_id": target_id,
                                "transactions_created": created,
                                "transactions_found": getattr(res, "rows_scanned", 0),
                                "skipped_existing": getattr(res, "skipped_existing", 0),
                                "errors": getattr(res, "errors", [])[:5],
                            }
                        )
                    else:
                        import tempfile
                        from pathlib import Path as P

                        from honestspend.services.excel_import import import_budget_xlsx

                        prof = session.get(Profile, profile_id)
                        slug = prof.slug if prof else "personal"
                        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                            tmp.write(content)
                            tmp_path = tmp.name
                        try:
                            res = import_budget_xlsx(session, P(tmp_path), profile_slug=slug)
                            results.append(
                                {
                                    "filename": fname,
                                    "format": "xlsx",
                                    "transactions_created": getattr(res, "transactions_created", 0),
                                    "errors": getattr(res, "errors", [])[:5],
                                }
                            )
                        finally:
                            try:
                                P(tmp_path).unlink(missing_ok=True)
                            except Exception:
                                pass
                except Exception as e:
                    results.append({"filename": fname, "format": "xlsx", "error": str(e)[:300]})
            else:
                results.append({"filename": fname, "format": ext, "error": "unsupported"})

    session.flush()
    total_skipped = 0
    for r in results:
        total_skipped += int(r.get("skipped_existing") or 0)
        if isinstance(r.get("accounts"), list):
            for a in r["accounts"]:
                if isinstance(a, dict):
                    total_skipped += int(a.get("skipped_existing") or 0)

    from honestspend.services.import_bootstrap import bootstrap_books_after_import
    from honestspend.services.import_dedupe import human_dup_summary

    bootstrap = bootstrap_books_after_import(
        session,
        profile_ids=list({int(v) for v in key_to_profile.values()}),
        lookback_days=730,
        auto_accept_recurring=True,
        auto_accept_discover=True,
        auto_categorize=True,
    )

    plain = human_dup_summary(
        created=total_created, skipped=total_skipped, files=len(files)
    )
    boot_msg = (bootstrap or {}).get("customer_message") or ""
    customer = (plain + " " + boot_msg).strip()
    return {
        "ok": True,
        "transactions_created": total_created,
        "duplicates_skipped": total_skipped,
        "results": results,
        "entities_resolved": key_to_profile,
        "bootstrap": bootstrap,
        "summary": f"{customer} Processed {len(files)} file(s).",
        "hint": (
            "We pulled balances, APR/promo/limits when statements include them, "
            "set up recurring bills and cards we could prove from history, "
            "categorized what we could, and skipped duplicates. "
            "Home for Safe to spend; Sort charges for anything left."
        ),
        "customer_message": customer,
    }
