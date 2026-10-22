"""
Pydantic v2 schemas for Transactions, Invoices, Policy Rules, and Audit Decisions.
"""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict

from src.models.state import DecisionStatus, SeverityLevel


def compute_audit_hash(data: Dict[str, Any]) -> str:
    """Computes a deterministic SHA-256 hash over canonical JSON representation."""
    canonical_json = json.dumps(data, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


class Invoice(BaseModel):
    """Invoice metadata for VAT reconciliation under German & EU commercial law."""
    model_config = ConfigDict(extra="ignore")

    invoice_number: Optional[str] = Field(default=None, description="Official supplier invoice reference")
    supplier_name: Optional[str] = Field(default=None, description="Full registered legal entity name")
    net_amount: float = Field(..., description="Net taxable amount in specified currency")
    vat_rate: float = Field(..., description="VAT rate as decimal (e.g. 0.19) or percent (19.0)")
    vat_amount: float = Field(..., description="Reported VAT tax amount")
    gross_amount: float = Field(..., description="Total gross payable amount")
    supplier_vat_id: Optional[str] = Field(default=None, description="Supplier tax identification (e.g. DE123456789)")
    invoice_date: Optional[str] = Field(default=None, description="Date of issuance (ISO 8601)")
    currency: str = Field(default="EUR", description="ISO 4217 Currency code")

    @field_validator("vat_rate", mode="before")
    @classmethod
    def normalize_vat_rate(cls, v: Any) -> float:
        val = float(v)
        # Normalize 19.0 or 7.0 to 0.19 and 0.07 if given as whole percentage
        if val > 1.0:
            return round(val / 100.0, 4)
        return val


class Transaction(BaseModel):
    """Spend transaction record submitted for automated policy evaluation."""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Unique transaction identifier")
    amount: float = Field(..., gt=0, description="Gross total spend amount in currency")
    currency: str = Field(default="EUR", description="ISO 4217 Currency code (default: EUR)")
    merchant: str = Field(..., description="Merchant / vendor name")
    category: str = Field(..., description="Expense category (e.g. Software, Client Entertainment, Hardware, Travel)")
    employee_id: str = Field(..., description="Originating employee identifier")
    receipt_url: Optional[str] = Field(default=None, description="Storage URI or URL to receipt image/PDF")
    vat_id: Optional[str] = Field(default=None, description="Associated merchant VAT identification number")
    attendees_count: Optional[int] = Field(default=1, ge=1, description="Number of participants for entertainment/meals")
    description: Optional[str] = Field(default=None, description="Expense business justification/memo")
    department: Optional[str] = Field(default="Engineering", description="Cost center / department")
    invoice_details: Optional[Invoice] = Field(default=None, description="Detailed itemized invoice breakdown")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return v.upper()


class PolicyRule(BaseModel):
    """Specification of a deterministic spend policy rule."""
    model_config = ConfigDict(extra="ignore")

    rule_id: str = Field(..., description="Identifier code (e.g. POL-001)")
    name: str = Field(..., description="Human-readable rule name")
    description: str = Field(..., description="Operational definition")
    category: Optional[str] = Field(default=None, description="Restricted expense category or None for global")
    severity: SeverityLevel = Field(default=SeverityLevel.REVIEW, description="Action severity when violated")


class AuditDecision(BaseModel):
    """Immutable audit trail decision emitted by the autonomous agent."""
    model_config = ConfigDict(extra="ignore")

    status: DecisionStatus = Field(..., description="Final audit verdict")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Agent confidence score")
    triggered_rules: List[str] = Field(default_factory=list, description="List of policy IDs triggered")
    explanation: str = Field(..., description="Plain-language justification for audit logging")
    audit_trail_hash: str = Field(..., description="Tamper-evident SHA-256 cryptographic proof")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic and tool execution metadata")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def create(
        cls,
        status: DecisionStatus,
        confidence_score: float,
        triggered_rules: List[str],
        explanation: str,
        transaction_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AuditDecision":
        ts = datetime.now(timezone.utc).isoformat()
        meta = metadata or {}
        hash_payload = {
            "transaction_id": transaction_id,
            "status": status.value if hasattr(status, "value") else str(status),
            "confidence_score": round(confidence_score, 4),
            "triggered_rules": sorted(triggered_rules),
            "explanation": explanation,
            "timestamp": ts,
            "metadata": meta,
        }
        trail_hash = compute_audit_hash(hash_payload)
        return cls(
            status=status,
            confidence_score=confidence_score,
            triggered_rules=triggered_rules,
            explanation=explanation,
            audit_trail_hash=trail_hash,
            metadata=meta,
            timestamp=ts,
        )
