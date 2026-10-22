"""
Deterministic spend policy engine for European corporate spend management (Moss).

Implements corporate financial governance:
- SaaS / Software auto-approval up to €500 (with valid receipt)
- Client Entertainment per-person caps (€150/head limit)
- Hard ceiling cap across all categories (> €1,500 requires human review)
- Receipt mandate policies (strict proof requirements for expenses > €250)
- Prohibited expense filtering
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.config import config
from src.models.state import DecisionStatus, SeverityLevel
from src.models.transaction import Transaction, PolicyRule


# Standard codified corporate policies
POLICIES = {
    "POL-SAAS-001": PolicyRule(
        rule_id="POL-SAAS-001",
        name="SaaS Auto-Approval Limit",
        description=f"Software subscriptions up to €{config.SAAS_AUTO_APPROVE_LIMIT:.2f} are auto-approved with receipt.",
        category="Software",
        severity=SeverityLevel.REVIEW,
    ),
    "POL-ENT-002": PolicyRule(
        rule_id="POL-ENT-002",
        name="Client Entertainment Per-Person Cap",
        description=f"Client entertainment cannot exceed €{config.ENTERTAINMENT_MAX_PER_PERSON:.2f} per attendee.",
        category="Client Entertainment",
        severity=SeverityLevel.REVIEW,
    ),
    "POL-CAP-003": PolicyRule(
        rule_id="POL-CAP-003",
        name="Hard Spend Ceiling",
        description=f"All transactions exceeding €{config.HARD_CEILING_AMOUNT:.2f} require secondary executive sign-off.",
        category=None,
        severity=SeverityLevel.HARD_STOP,
    ),
    "POL-REC-004": PolicyRule(
        rule_id="POL-REC-004",
        name="Receipt Proof Mandate",
        description="Expenses over €250.00 require a valid attached receipt for tax compliance.",
        category=None,
        severity=SeverityLevel.REJECT,
    ),
    "POL-CAT-005": PolicyRule(
        rule_id="POL-CAT-005",
        name="Prohibited Personal / Luxury Spend",
        description="Personal luxury or unapproved non-business items are strictly prohibited.",
        category=None,
        severity=SeverityLevel.REJECT,
    ),
}

# Merchant or description keywords indicating prohibited personal/luxury spend
PROHIBITED_KEYWORDS = [
    "luxury",
    "kadewe",
    "casino",
    "gambling",
    "personal jewelry",
    "spa wellness",
]


class PolicyEvaluationResult(BaseModel):
    """Result of evaluating a transaction against corporate spend policies."""
    is_compliant: bool = Field(..., description="True if transaction satisfies all auto-approval criteria")
    suggested_status: DecisionStatus = Field(..., description="Target status: APPROVED, REJECTED, or PENDING_HUMAN_REVIEW")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this evaluation")
    violations: List[str] = Field(default_factory=list, description="Descriptive policy violation messages")
    triggered_rules: List[str] = Field(default_factory=list, description="IDs of triggered policy rules")
    requires_human_review: bool = Field(..., description="Flag indicating human sign-off needed")
    is_rejected: bool = Field(..., description="Flag indicating outright policy rejection")
    details: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic and breakdown metrics")


class PolicyEngine:
    """Evaluates transactions against deterministic corporate spend policies."""

    @classmethod
    def evaluate(cls, tx: Transaction) -> PolicyEvaluationResult:
        violations: List[str] = []
        triggered_rules: List[str] = []
        requires_human_review = False
        is_rejected = False

        category_lower = tx.category.strip().lower()
        merchant_lower = tx.merchant.strip().lower()
        desc_lower = (tx.description or "").strip().lower()

        # Check 1: Prohibited Merchant or Keywords (Strict REJECT)
        combined_text = f"{merchant_lower} {desc_lower}"
        for kw in PROHIBITED_KEYWORDS:
            if kw in combined_text:
                is_rejected = True
                triggered_rules.append("POL-CAT-005")
                violations.append(
                    f"Prohibited spend detected: '{kw}' violates corporate expense policy (Merchant: {tx.merchant})"
                )
                break

        # Check 2: Missing Receipt Proof
        has_receipt = bool(tx.receipt_url and tx.receipt_url.strip())
        if not has_receipt:
            if tx.amount > config.KLEINBETRAGSRECHNUNG_MAX:
                is_rejected = True
                triggered_rules.append("POL-REC-004")
                violations.append(
                    f"Missing mandatory receipt on transaction of €{tx.amount:.2f} "
                    f"(Receipt required for all expenses > €{config.KLEINBETRAGSRECHNUNG_MAX:.2f})"
                )
            else:
                requires_human_review = True
                triggered_rules.append("POL-REC-004")
                violations.append(f"Receipt missing for €{tx.amount:.2f} transaction")

        # Check 3: Universal Hard Ceiling (> €1,500)
        if tx.amount > config.HARD_CEILING_AMOUNT:
            requires_human_review = True
            triggered_rules.append("POL-CAP-003")
            violations.append(
                f"Transaction amount €{tx.amount:.2f} exceeds hard ceiling of €{config.HARD_CEILING_AMOUNT:.2f}; "
                f"secondary human sign-off strictly required"
            )

        # Check 4: Client Entertainment Cap (€150/person)
        if "entertainment" in category_lower or "client" in category_lower:
            attendees = max(tx.attendees_count or 1, 1)
            cost_per_person = tx.amount / attendees
            if cost_per_person > config.ENTERTAINMENT_MAX_PER_PERSON:
                requires_human_review = True
                triggered_rules.append("POL-ENT-002")
                violations.append(
                    f"Client entertainment expense of €{cost_per_person:.2f}/person "
                    f"({attendees} attendees, €{tx.amount:.2f} total) exceeds cap of "
                    f"€{config.ENTERTAINMENT_MAX_PER_PERSON:.2f}/person"
                )

        # Check 5: SaaS / Software Auto-Approval Limit (€500)
        if any(w in category_lower for w in ["software", "saas", "cloud"]):
            if tx.amount > config.SAAS_AUTO_APPROVE_LIMIT and not (tx.amount > config.HARD_CEILING_AMOUNT):
                # If above SaaS limit but below hard ceiling, still requires manager review
                requires_human_review = True
                triggered_rules.append("POL-SAAS-001")
                violations.append(
                    f"SaaS subscription €{tx.amount:.2f} exceeds auto-approval threshold of "
                    f"€{config.SAAS_AUTO_APPROVE_LIMIT:.2f}; requires departmental review"
                )

        # Check 6: Per Diem cap
        if "per diem" in category_lower:
            if tx.amount > 100.0:
                requires_human_review = True
                violations.append(f"Per diem of €{tx.amount:.2f} exceeds standard daily allowance cap")

        # Determine suggested status and confidence
        if is_rejected:
            suggested_status = DecisionStatus.REJECTED
            confidence = 0.99
            is_compliant = False
        elif requires_human_review:
            suggested_status = DecisionStatus.PENDING_HUMAN_REVIEW
            confidence = 0.95
            is_compliant = False
        else:
            suggested_status = DecisionStatus.APPROVED
            confidence = 0.98
            is_compliant = True

        return PolicyEvaluationResult(
            is_compliant=is_compliant,
            suggested_status=suggested_status,
            confidence=confidence,
            violations=violations,
            triggered_rules=list(dict.fromkeys(triggered_rules)),
            requires_human_review=requires_human_review,
            is_rejected=is_rejected,
            details={
                "amount": tx.amount,
                "category": tx.category,
                "attendees": tx.attendees_count,
                "has_receipt": has_receipt,
            },
        )
