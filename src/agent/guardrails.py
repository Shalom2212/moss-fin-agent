"""
Non-LLM deterministic security and confidence threshold validator for FinOps.

Enforces:
1. Confidence Threshold Gate: If confidence < 0.90, forces PENDING_HUMAN_REVIEW.
2. Compliance Hard Stops: Universal €1,500 cap, €150/head entertainment cap, budget overruns.
3. Tax Integrity: Catches § 14 UStG VAT failures and bogus VAT IDs.
4. Ledger Export Gating: Strictly blocks General Ledger export if status is not APPROVED.
5. Cryptographic Audit Proof: Validates deterministic SHA-256 audit trail hashes.
"""

from typing import Any, Dict, List, Optional
from src.config import config
from src.models.state import DecisionStatus
from src.models.transaction import Transaction, AuditDecision, compute_audit_hash


class GuardrailViolationError(Exception):
    """Raised when an action violates a critical financial security guardrail."""
    pass


class GuardrailCheckResult:
    """Result of deterministic guardrail enforcement."""
    def __init__(
        self,
        passed: bool,
        final_status: DecisionStatus,
        effective_confidence: float,
        triggered_guardrails: List[str],
        reasons: List[str],
    ):
        self.passed = passed
        self.final_status = final_status
        self.effective_confidence = effective_confidence
        self.triggered_guardrails = triggered_guardrails
        self.reasons = reasons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "final_status": self.final_status.value,
            "effective_confidence": self.effective_confidence,
            "triggered_guardrails": self.triggered_guardrails,
            "reasons": self.reasons,
        }


class DeterministicGuardrailValidator:
    """
    Zero-trust deterministic safety validator operating independently of LLM outputs.
    """

    @classmethod
    def validate_confidence_threshold(cls, raw_confidence: float) -> bool:
        """Enforces minimum confidence threshold (0.90)."""
        return raw_confidence >= config.CONFIDENCE_THRESHOLD

    @classmethod
    def validate_ledger_export_precondition(cls, status: DecisionStatus) -> None:
        """
        Hard block: Prevents any transaction from being exported to GL unless APPROVED.
        """
        if status != DecisionStatus.APPROVED:
            raise GuardrailViolationError(
                f"CRITICAL SECURITY GUARDRAIL: Ledger export blocked! "
                f"Status is '{status.value}', but only strictly 'APPROVED' transactions can be booked."
            )

    @classmethod
    def apply_guardrails(
        cls,
        tx: Transaction,
        preliminary_status: DecisionStatus,
        confidence: float,
        policy_violations: List[str],
        vat_valid: bool,
        vat_errors: List[str],
        budget_exceeded: bool,
    ) -> GuardrailCheckResult:
        """
        Applies zero-trust deterministic safety rules on top of engine evaluations.
        """
        reasons: List[str] = []
        triggered: List[str] = []
        final_status = preliminary_status
        effective_confidence = confidence

        # Guardrail 1: VAT Integrity Guardrail
        if not vat_valid and vat_errors:
            # Check if this is a severe math error or bogus VAT ID
            is_severe = any(
                "calculation mismatch" in e.lower() or "invalid vat id format" in e.lower()
                for e in vat_errors
            )
            triggered.append("GUARD-VAT-001")
            reasons.extend(vat_errors)
            if is_severe:
                final_status = DecisionStatus.REJECTED
                effective_confidence = max(effective_confidence, 0.99)
            else:
                final_status = DecisionStatus.PENDING_HUMAN_REVIEW
                effective_confidence = min(effective_confidence, 0.85)

        # Guardrail 2: Hard Ceiling Guardrail (> €1,500)
        if tx.amount > config.HARD_CEILING_AMOUNT:
            triggered.append("GUARD-CAP-002")
            msg = f"Amount €{tx.amount:.2f} breaches universal €{config.HARD_CEILING_AMOUNT:.2f} ceiling"
            reasons.append(msg)
            if final_status != DecisionStatus.REJECTED:
                final_status = DecisionStatus.PENDING_HUMAN_REVIEW

        # Guardrail 3: Client Entertainment Cap (€150/person)
        if "entertainment" in tx.category.lower() or "client" in tx.category.lower():
            attendees = max(tx.attendees_count or 1, 1)
            cost_per_person = tx.amount / attendees
            if cost_per_person > config.ENTERTAINMENT_MAX_PER_PERSON:
                triggered.append("GUARD-ENT-003")
                msg = f"Entertainment cost of €{cost_per_person:.2f}/person exceeds €{config.ENTERTAINMENT_MAX_PER_PERSON:.2f} limit"
                reasons.append(msg)
                if final_status != DecisionStatus.REJECTED:
                    final_status = DecisionStatus.PENDING_HUMAN_REVIEW

        # Guardrail 4: Missing Receipt for High Amount (> €250)
        has_receipt = bool(tx.receipt_url and tx.receipt_url.strip())
        if not has_receipt:
            triggered.append("GUARD-REC-004")
            if tx.amount > config.KLEINBETRAGSRECHNUNG_MAX:
                reasons.append(f"Mandatory receipt missing for €{tx.amount:.2f} expense")
                final_status = DecisionStatus.REJECTED
            else:
                reasons.append(f"Receipt missing for small expense €{tx.amount:.2f}")
                if final_status != DecisionStatus.REJECTED:
                    final_status = DecisionStatus.PENDING_HUMAN_REVIEW

        # Guardrail 5: Budget Overrun
        if budget_exceeded:
            triggered.append("GUARD-BUD-005")
            reasons.append(f"Department budget exceeded for department '{tx.department}'")
            if final_status != DecisionStatus.REJECTED:
                final_status = DecisionStatus.PENDING_HUMAN_REVIEW

        # Guardrail 6: Confidence Threshold Gate
        if not cls.validate_confidence_threshold(effective_confidence):
            triggered.append("GUARD-CONF-006")
            reasons.append(
                f"Confidence score {effective_confidence:.2f} below required threshold "
                f"({config.CONFIDENCE_THRESHOLD:.2f}); routing to human controller"
            )
            if final_status != DecisionStatus.REJECTED:
                final_status = DecisionStatus.PENDING_HUMAN_REVIEW

        passed = (final_status == DecisionStatus.APPROVED)

        return GuardrailCheckResult(
            passed=passed,
            final_status=final_status,
            effective_confidence=effective_confidence,
            triggered_guardrails=triggered,
            reasons=reasons,
        )

    @classmethod
    def verify_audit_trail_tamper_proofing(cls, decision: AuditDecision, transaction_id: str) -> bool:
        """Verifies that an audit decision's hash matches canonical recalculation."""
        recomputed_payload = {
            "transaction_id": transaction_id,
            "status": decision.status.value,
            "confidence_score": round(decision.confidence_score, 4),
            "triggered_rules": sorted(decision.triggered_rules),
            "explanation": decision.explanation,
            "timestamp": decision.timestamp,
            "metadata": decision.metadata,
        }
        expected_hash = compute_audit_hash(recomputed_payload)
        return decision.audit_trail_hash == expected_hash
