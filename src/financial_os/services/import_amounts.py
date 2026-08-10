"""Shared import amount conventions (CSV / PDF / OFX / credit card honesty).

Ledger rules (see account_balance.apply_amount_to_account):
  · Cash: expenses negative, deposits positive.
  · Credit: charges (spend) negative → owed increases;
    payments and merchant credits/refunds positive → owed decreases.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# Strong payment cues (prefer these over bare "payment")
_PAYMENT_STRONG = re.compile(
    r"(?i)\b("
    r"autopay|auto[\s\-]?pay|thank\s*you|"
    r"online\s*p(?:ay(?:ment)?|mt|ymt)|"
    r"ach\s*p(?:ay(?:ment)?|mt|ymt)|"
    r"pmt|pymt|"
    r"bill\s*pay|mobile\s*pay|web\s*pay|"
    r"card\s*payment|payment\s*received|"
    r"payment\s*[\-–—]\s*thank|payment\s*thank"
    r")\b"
)
_PAYMENT_WORD = re.compile(r"(?i)\bpayment\b")

# Fees / products that contain "payment" but are charges
_NOT_PAYMENT = re.compile(
    r"(?i)\b("
    r"fee|interest|finance\s*charge|"
    r"late\s*(fee|payment)|"
    r"penalty|nsf|overlimit|"
    r"protection|insurance|warranty|"
    r"payment\s*plan"
    r")\b"
)

# Merchant refunds / statement credits (reduce owed; keep positive)
_REFUND_HINT = re.compile(
    r"(?i)\b("
    r"refund|return|reversal|"
    r"statement\s*credit|price\s*adjustment|"
    r"cash\s*back|cashback|"
    r"merchandise\s*credit|store\s*credit|"
    r"credit\s*adjustment|credit\s*memo|"
    r"purchase\s*return|vendor\s*credit"
    r")\b"
)


def looks_like_credit_payment(payee: str | None) -> bool:
    """True when payee looks like a card payment (reduce owed), not a fee/product."""
    p = (payee or "").strip()
    if not p or _NOT_PAYMENT.search(p):
        return False
    if _PAYMENT_STRONG.search(p):
        return True
    # Bare "payment" only if not a product name (protection already in _NOT_PAYMENT)
    return bool(_PAYMENT_WORD.search(p))


def looks_like_credit_refund(payee: str | None) -> bool:
    """Merchant refund / statement credit — reduce owed (positive ledger)."""
    p = (payee or "").strip()
    if not p:
        return False
    # Payments win over refund if both match (e.g. "payment return")
    if looks_like_credit_payment(p):
        return False
    return bool(_REFUND_HINT.search(p))


def normalize_credit_import_amount(amount: Decimal | Any, payee: str | None = "") -> Decimal:
    """Map credit export amount into ledger convention for apply_amount_to_account.

    Handles both common CSV conventions:
      · Charges + / payments −  (issuer export)
      · Charges − / payments +  (bank-signed)
    Payments and refunds always end positive; other positives become charges (negative).
    """
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if looks_like_credit_payment(payee) or looks_like_credit_refund(payee):
        return abs(amt)
    if amt > 0:
        return -amt  # charge listed as positive on many card CSVs/PDFs
    return amt
