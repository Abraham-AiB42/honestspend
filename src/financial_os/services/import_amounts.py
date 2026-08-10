"""Shared import amount conventions (CSV / PDF / credit card honesty).

Ledger rules (see account_balance.apply_amount_to_account):
  · Cash: expenses negative, deposits positive.
  · Credit: charges (spend) negative → owed increases; payments positive → owed decreases.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# Card statement / CSV payment rows (not late fees, interest, etc.)
_PAYMENT_HINT = re.compile(
    r"(?i)\b("
    r"payment|autopay|auto[\s\-]?pay|thank\s*you|"
    r"online\s*p(?:ay(?:ment)?|mt)|ach\s*p(?:ay(?:ment)?|mt)|"
    r"pmt|bill\s*pay|mobile\s*pay|web\s*pay|card\s*payment|"
    r"payment\s*received|payment\s*\-\s*thank"
    r")\b"
)
_NOT_PAYMENT = re.compile(
    r"(?i)\b(fee|interest|finance\s*charge|late\s*fee|penalty|nsf|overlimit)\b"
)


def looks_like_credit_payment(payee: str | None) -> bool:
    """True when payee looks like a card payment, not a fee or purchase."""
    p = (payee or "").strip()
    if not p or _NOT_PAYMENT.search(p):
        return False
    return bool(_PAYMENT_HINT.search(p))


def normalize_credit_import_amount(amount: Decimal | Any, payee: str | None = "") -> Decimal:
    """Map credit export amount into ledger convention for apply_amount_to_account.

    Handles both common CSV conventions:
      · Charges + / payments −  (issuer export)
      · Charges − / payments +  (bank-signed)
    Payments always end positive; non-payment positives become charges (negative).
    """
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if looks_like_credit_payment(payee):
        return abs(amt)
    if amt > 0:
        return -amt  # charge listed as positive on many card CSVs/PDFs
    return amt
