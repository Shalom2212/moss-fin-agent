"""
Unit tests for Deterministic Guardrails, HITL Gate, and Orchestrator.
"""

import pytest
from src.agent.guardrails import (
    DeterministicGuardrailValidator,
    GuardrailViolationError,
)
from src.agent.orchestrator import SpendReconciliationAgent
from src.models.state import DecisionStatus
from src.models.transaction import Transaction, Invoice


def test_guardrail_blocks_gl_export_if_not_approved():
    """Deterministic guardrail raises error if attempting to export unapproved transaction."""
    with pytest.raises(GuardrailViolationError):
        DeterministicGuardrailValidator.validate_ledger_export_precondition(
            DecisionStatus.PENDING_HUMAN_REVIEW
        )

    with pytest.raises(GuardrailViolationError):
        DeterministicGuardrailValidator.validate_ledger_export_precondition(
            DecisionStatus.REJECTED
        )

    # Should not raise for APPROVED
    DeterministicGuardrailValidator.validate_ledger_export_precondition(
        DecisionStatus.APPROVED
    )


def test_confidence_threshold_guardrail():
    """Confidence < 0.90 must fail confidence gate."""
    assert DeterministicGuardrailValidator.validate_confidence_threshold(0.95) is True
    assert DeterministicGuardrailValidator.validate_confidence_threshold(0.89) is False


def test_orchestrator_approves_compliant_transaction():
    """Orchestrator runs tools and approves compliant transaction, posting to GL."""
    agent = SpendReconciliationAgent()
    tx = Transaction(
        id="TEST-ORCH-APP",
        amount=250.0,
        currency="EUR",
        merchant="Atlassian B.V.",
        category="Software",
        employee_id="EMP-1",
        receipt_url="https://receipts.corp/jira.pdf",
        vat_id="NL820739453B01",
        invoice_details=Invoice(
            net_amount=210.08,
            vat_rate=0.19,
            vat_amount=39.92,
            gross_amount=250.0,
            supplier_vat_id="NL820739453B01",
        ),
    )
    decision = agent.process_transaction(tx)
    assert decision.status == DecisionStatus.APPROVED
    assert decision.confidence_score >= 0.90
    assert decision.metadata["posted_to_gl"] is True
    assert DeterministicGuardrailValidator.verify_audit_trail_tamper_proofing(decision, tx.id) is True


def test_orchestrator_routes_to_hitl_for_hard_cap():
    """Transaction > €1,500 is routed to PENDING_HUMAN_REVIEW and not posted to GL."""
    agent = SpendReconciliationAgent()
    tx = Transaction(
        id="TEST-ORCH-HITL",
        amount=3200.0,
        currency="EUR",
        merchant="Apple Store",
        category="Hardware",
        employee_id="EMP-1",
        receipt_url="https://receipts.corp/mac.pdf",
        vat_id="DE113840122",
    )
    decision = agent.process_transaction(tx)
    assert decision.status == DecisionStatus.PENDING_HUMAN_REVIEW
    assert decision.metadata["posted_to_gl"] is False
    assert decision.metadata["hitl_queued"] is True
