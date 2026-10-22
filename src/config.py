"""
Configuration module for moss-fin-agent spend policies and tax compliance parameters.
"""

from typing import Dict


class PolicyConfig:
    # Category Limits
    SAAS_AUTO_APPROVE_LIMIT: float = 500.0  # Max auto-approval for SaaS/Software
    ENTERTAINMENT_MAX_PER_PERSON: float = 150.0  # Cap per head for client entertainment
    HARD_CEILING_AMOUNT: float = 1500.0  # Strict secondary human review cap across all categories

    # German Tax Regulations (§ 14 UStG & § 33 UStDV)
    KLEINBETRAGSRECHNUNG_MAX: float = 250.0  # Small amount invoice threshold (§ 33 UStDV)
    STANDARD_VAT_RATE_DE: float = 0.19  # Standard German VAT rate (19%)
    REDUCED_VAT_RATE_DE: float = 0.07  # Reduced German VAT rate (7%)
    ZERO_VAT_RATE: float = 0.0  # Reverse charge or tax-exempt
    VAT_ROUNDING_TOLERANCE: float = 0.05  # Tolerated cent rounding differences in invoices

    # Guardrails
    CONFIDENCE_THRESHOLD: float = 0.90  # Any automated score below this requires human sign-off

    # German Chart of Accounts (SKR03 / SKR04 Mapping)
    GL_ACCOUNT_MAPPING: Dict[str, str] = {
        "Software": "4900",  # IT / Software subscriptions
        "SaaS": "4900",
        "Cloud": "4900",
        "Client Entertainment": "4650",  # Bewirtungsaufwendungen
        "Entertainment": "4650",
        "Hardware": "0410",  # Office & computer equipment (Assets)
        "Travel": "4670",  # Reisekosten
        "Office Supplies": "4930",  # Bürobedarf
        "Marketing": "4600",  # Werbekosten
        "Per Diem": "4673",  # Verpflegungsmehraufwand
    }

    # Department Budgets (Default quarterly allowances in EUR)
    DEFAULT_DEPARTMENT_BUDGETS: Dict[str, float] = {
        "Engineering": 75000.0,
        "Marketing": 40000.0,
        "Sales": 35000.0,
        "Operations": 25000.0,
        "Product": 30000.0,
        "General": 20000.0,
    }


config = PolicyConfig()
