"""
German & EU VAT Compliance Engine adhering to § 14 UStG and § 33 UStDV.

Features:
- VAT ID / UID format verification (German DE + 9 digits, and EU VIES standard formats)
- Statutory tax rate enforcement (Standard 19%, Reduced 7%, Reverse Charge 0%)
- Mathematical reconciliation: Net Amount + VAT Amount == Gross Amount
- § 33 UStDV Kleinbetragsrechnungen (Invoices <= €250) vs full invoices (> €250)
"""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.config import config
from src.models.transaction import Invoice


# Regex patterns for EU VAT Identification Numbers (UID)
EU_VAT_PATTERNS: Dict[str, str] = {
    "DE": r"^DE[0-9]{9}$",  # Germany: DE followed by 9 digits
    "AT": r"^ATU[0-9]{8}$",  # Austria: ATU followed by 8 digits
    "BE": r"^BE[0-1][0-9]{9}$",  # Belgium: BE0/BE1 + 9 digits
    "FR": r"^FR[0-9A-Z]{2}[0-9]{9}$",  # France: FR + 2 alphanumeric + 9 digits
    "NL": r"^NL[0-9]{9}B[0-9]{2}$",  # Netherlands: NL + 9 digits + B + 2 digits
    "ES": r"^ES[0-9A-Z][0-9]{7}[0-9A-Z]$",  # Spain
    "IT": r"^IT[0-9]{11}$",  # Italy: IT + 11 digits
    "PL": r"^PL[0-9]{10}$",  # Poland: PL + 10 digits
    "IE": r"^IE[0-9]{7}[A-W][A-I]?$",  # Ireland
    "DK": r"^DK[0-9]{8}$",  # Denmark
    "SE": r"^SE[0-9]{12}$",  # Sweden
}


class VATValidationResult(BaseModel):
    """Evaluation result for VAT compliance."""
    is_valid: bool = Field(..., description="Overall VAT compliance boolean")
    is_kleinbetragsrechnung: bool = Field(..., description="Whether expense falls under § 33 UStDV (<= €250)")
    tax_rate_applied: float = Field(..., description="Effective VAT rate")
    vat_id_valid: bool = Field(..., description="Format validity of the VAT number")
    math_consistent: bool = Field(..., description="Whether Net + VAT = Gross holds mathematically")
    errors: List[str] = Field(default_factory=list, description="Specific compliance errors found")
    details: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic data")


class GermanVATValidator:
    """Validator implementing German Umsatzsteuergesetz (§ 14 UStG)."""

    @classmethod
    def validate_vat_id(cls, vat_id: Optional[str]) -> bool:
        """
        Validates the format of an EU VAT ID, prioritizing German DE UID.
        """
        if not vat_id:
            return False
        clean_id = vat_id.strip().upper().replace(" ", "").replace("-", "")
        country = clean_id[:2]
        pattern = EU_VAT_PATTERNS.get(country)
        if pattern:
            return bool(re.match(pattern, clean_id))
        
        # Generic fallback for other EU member states: 2 letter country code + 5 to 12 alphanumeric
        generic_eu_pattern = r"^[A-Z]{2}[0-9A-Z]{5,12}$"
        return bool(re.match(generic_eu_pattern, clean_id))

    @classmethod
    def validate_invoice(
        cls,
        gross_amount: float,
        net_amount: Optional[float] = None,
        vat_amount: Optional[float] = None,
        vat_rate: Optional[float] = None,
        vat_id: Optional[str] = None,
        invoice: Optional[Invoice] = None,
    ) -> VATValidationResult:
        """
        Comprehensive § 14 UStG evaluation for an invoice or transaction.
        """
        errors: List[str] = []

        # If full Invoice object provided, extract values
        if invoice is not None:
            gross_amount = invoice.gross_amount
            net_amount = invoice.net_amount
            vat_amount = invoice.vat_amount
            vat_rate = invoice.vat_rate
            vat_id = vat_id or invoice.supplier_vat_id

        is_kleinbetrag = gross_amount <= config.KLEINBETRAGSRECHNUNG_MAX
        vat_id_valid = False

        # 1. VAT ID verification
        if vat_id:
            vat_id_valid = cls.validate_vat_id(vat_id)
            if not vat_id_valid:
                errors.append(f"Invalid VAT ID format '{vat_id}' - fails EU/DE USt-IdNr specification")
        else:
            if not is_kleinbetrag:
                errors.append(
                    f"Missing supplier VAT ID on transaction > €{config.KLEINBETRAGSRECHNUNG_MAX:.2f} "
                    f"(Mandatory under § 14 Abs. 4 Nr. 2 UStG)"
                )

        # 2. Check Tax Rate if amounts are present
        effective_rate = vat_rate if vat_rate is not None else config.STANDARD_VAT_RATE_DE
        valid_rates = [
            config.STANDARD_VAT_RATE_DE,
            config.REDUCED_VAT_RATE_DE,
            config.ZERO_VAT_RATE,
        ]

        if vat_rate is not None:
            # Check if rate matches statutory German rates
            rate_matches = any(abs(vat_rate - vr) < 0.005 for vr in valid_rates)
            if not rate_matches:
                errors.append(f"Non-statutory VAT rate: {vat_rate * 100:.1f}%. Permitted: 19%, 7%, or 0%")

        # 3. Mathematical Reconciliation (§ 14 Abs. 4 Nr. 7 & 8 UStG)
        math_consistent = True
        if net_amount is not None and vat_amount is not None:
            expected_gross = net_amount + vat_amount
            gross_diff = abs(expected_gross - gross_amount)
            if gross_diff > config.VAT_ROUNDING_TOLERANCE:
                math_consistent = False
                errors.append(
                    f"§ 14 UStG calculation mismatch: Net (€{net_amount:.2f}) + "
                    f"VAT (€{vat_amount:.2f}) = €{expected_gross:.2f}, but gross is €{gross_amount:.2f} "
                    f"(Difference: €{gross_diff:.2f} exceeds tolerance €{config.VAT_ROUNDING_TOLERANCE:.2f})"
                )

            # Check VAT amount against rate
            if vat_rate is not None:
                expected_vat = net_amount * vat_rate
                vat_diff = abs(expected_vat - vat_amount)
                if vat_diff > config.VAT_ROUNDING_TOLERANCE:
                    math_consistent = False
                    errors.append(
                        f"VAT amount mismatch: Net (€{net_amount:.2f}) * {vat_rate*100:.1f}% = "
                        f"€{expected_vat:.2f}, but reported VAT is €{vat_amount:.2f}"
                    )
        elif net_amount is not None and vat_rate is not None:
            calculated_vat = round(net_amount * vat_rate, 2)
            calculated_gross = round(net_amount + calculated_vat, 2)
            if abs(calculated_gross - gross_amount) > config.VAT_ROUNDING_TOLERANCE:
                math_consistent = False
                errors.append(
                    f"Calculated gross €{calculated_gross:.2f} deviates from reported gross €{gross_amount:.2f}"
                )

        is_valid = len(errors) == 0

        return VATValidationResult(
            is_valid=is_valid,
            is_kleinbetragsrechnung=is_kleinbetrag,
            tax_rate_applied=effective_rate,
            vat_id_valid=vat_id_valid,
            math_consistent=math_consistent,
            errors=errors,
            details={
                "gross_amount": gross_amount,
                "net_amount": net_amount,
                "vat_amount": vat_amount,
                "vat_id": vat_id,
                "is_kleinbetragsrechnung": is_kleinbetrag,
            },
        )
