"""Credit import amount convention helpers."""

from decimal import Decimal

from financial_os.services.import_amounts import (
    looks_like_credit_payment,
    looks_like_credit_refund,
    normalize_credit_import_amount,
)


def test_payment_keywords():
    assert looks_like_credit_payment("PAYMENT THANK YOU")
    assert looks_like_credit_payment("AUTOPAY")
    assert looks_like_credit_payment("Online Payment")
    assert looks_like_credit_payment("PAYMENT")
    assert looks_like_credit_payment("PYMT RCVD")
    assert not looks_like_credit_payment("AMAZON.COM")
    assert not looks_like_credit_payment("LATE PAYMENT FEE")
    assert not looks_like_credit_payment("LATE PAYMENT")
    assert not looks_like_credit_payment("INTEREST CHARGE")
    assert not looks_like_credit_payment("PAYMENT PROTECTION")
    assert not looks_like_credit_payment("PAYMENT PLAN FEE")


def test_refund_keywords():
    assert looks_like_credit_refund("AMAZON REFUND")
    assert looks_like_credit_refund("RETURN")
    assert looks_like_credit_refund("PRICE ADJUSTMENT")
    assert looks_like_credit_refund("STATEMENT CREDIT")
    assert not looks_like_credit_refund("AMAZON.COM")
    assert not looks_like_credit_refund("PAYMENT THANK YOU")  # payment wins


def test_normalize_charges_positive_payments_negative():
    assert normalize_credit_import_amount(Decimal("45.99"), "AMAZON") == Decimal("-45.99")
    assert normalize_credit_import_amount(Decimal("-100"), "PAYMENT THANK YOU") == Decimal("100")
    assert normalize_credit_import_amount(Decimal("100"), "PAYMENT") == Decimal("100")


def test_normalize_refund_stays_positive():
    assert normalize_credit_import_amount(Decimal("50.00"), "AMAZON REFUND") == Decimal("50.00")
    assert normalize_credit_import_amount(Decimal("-25.00"), "RETURN") == Decimal("25.00")


def test_normalize_bank_signed_credit():
    assert normalize_credit_import_amount(Decimal("-45.99"), "SHELL") == Decimal("-45.99")
    assert normalize_credit_import_amount(Decimal("100"), "AUTOPAY PAYMENT") == Decimal("100")


def test_normalize_payment_protection_is_charge():
    # Product name must not force a pay-down
    assert normalize_credit_import_amount(Decimal("9.99"), "PAYMENT PROTECTION") == Decimal("-9.99")
