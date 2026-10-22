"""
Unit tests for German § 14 UStG & § 33 UStDV VAT validation engine.
"""

import pytest
from src.engine.vat_validator import GermanVATValidator
from src.models.transaction import Invoice


def test_german_vat_id_format():
    """Validates German DE + 9 digits UID format."""
    assert GermanVATValidator.validate_vat_id("DE123456789") is True
    assert GermanVATValidator.validate_vat_id("DE812345678") is True
    assert GermanVATValidator.validate_vat_id("DE 123 456 789") is True

    # Invalid formats
    assert GermanVATValidator.validate_vat_id("DE12345") is False  # Too short
    assert GermanVATValidator.validate_vat_id("DE1234567890") is False  # Too long
    assert GermanVATValidator.validate_vat_id("123456789") is False  # Missing DE
    assert GermanVATValidator.validate_vat_id("") is False
    assert GermanVATValidator.validate_vat_id(None) is False


def test_eu_vat_id_formats():
    """Validates partner EU VAT ID patterns."""
    assert GermanVATValidator.validate_vat_id("ATU12345678") is True  # Austria
    assert GermanVATValidator.validate_vat_id("NL820739453B01") is True  # Netherlands
    assert GermanVATValidator.validate_vat_id("IE3351982T") is True  # Ireland
    assert GermanVATValidator.validate_vat_id("FRXX9999") is False  # Malformed French


def test_kleinbetragsrechnung_under_250():
    """Small invoices <= €250 are exempt from supplier VAT ID mandate (§ 33 UStDV)."""
    res = GermanVATValidator.validate_invoice(
        gross_amount=150.0,
        vat_id=None,  # No VAT ID
    )
    assert res.is_kleinbetragsrechnung is True
    assert res.is_valid is True  # Valid because under €250 exemption


def test_large_invoice_requires_vat_id():
    """Invoices > €250 without VAT ID violate § 14 UStG."""
    res = GermanVATValidator.validate_invoice(
        gross_amount=500.0,
        vat_id=None,
    )
    assert res.is_kleinbetragsrechnung is False
    assert res.is_valid is False
    assert any("Missing supplier VAT ID" in err for err in res.errors)


def test_math_reconciliation_success():
    """Consistent Net + VAT = Gross passes § 14 UStG check."""
    inv = Invoice(
        net_amount=100.0,
        vat_rate=0.19,
        vat_amount=19.0,
        gross_amount=119.0,
        supplier_vat_id="DE123456789",
    )
    res = GermanVATValidator.validate_invoice(
        gross_amount=119.0,
        invoice=inv,
    )
    assert res.is_valid is True
    assert res.math_consistent is True
    assert len(res.errors) == 0


def test_math_reconciliation_failure():
    """Inconsistent Net + VAT != Gross fails § 14 UStG check."""
    inv = Invoice(
        net_amount=800.0,
        vat_rate=0.19,
        vat_amount=100.0,  # 800 + 100 = 900 != 1190
        gross_amount=1190.0,
        supplier_vat_id="DE123456789",
    )
    res = GermanVATValidator.validate_invoice(
        gross_amount=1190.0,
        invoice=inv,
    )
    assert res.is_valid is False
    assert res.math_consistent is False
    assert any("calculation mismatch" in err for err in res.errors)


def test_reduced_statutory_vat_rate():
    """7% reduced statutory rate for German passenger rail passes."""
    inv = Invoice(
        net_amount=83.18,
        vat_rate=0.07,
        vat_amount=5.82,
        gross_amount=89.00,
        supplier_vat_id="DE115867422",
    )
    res = GermanVATValidator.validate_invoice(
        gross_amount=89.00,
        invoice=inv,
    )
    assert res.is_valid is True
    assert res.math_consistent is True
