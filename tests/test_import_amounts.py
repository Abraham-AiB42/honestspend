"""Credit import amount convention helpers."""

from decimal import Decimal

from financial_os.services.import_amounts import (
    looks_like_credit_payment,
    normalize_credit_import_amount,
)


def test_payment_keywords():
    assert looks_like_credit_payment("PAYMENT THANK YOU")
    assert looks_like_credit_payment("AUTOPAY")
    assert looks_like_credit_payment("Online Payment")
    assert not looks_like_credit_payment("AMAZON.COM")
    assert not looks_like_credit_payment("LATE PAYMENT FEE")
    assert not looks_like_credit_payment("INTEREST CHARGE")


def test_normalize_charges_positive_payments_negative():
    assert normalize_credit_import_amount(Decimal("45.99"), "AMAZON") == Decimal("-45.99")
    assert normalize_credit_import_amount(Decimal("-100"), "PAYMENT THANK YOU") == Decimal("100")


def test_normalize_bank_signed_credit():
    assert normalize_credit_import_amount(Decimal("-45.99"), "SHELL") == Decimal("-45.99")
    assert normalize_credit_import_amount(Decimal("100"), "AUTOPAY PAYMENT") == Decimal("100")
