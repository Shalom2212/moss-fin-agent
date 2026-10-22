"""
Unit tests for deterministic spend policy engine.
"""

import pytest
from src.config import config
from src.engine.policy_rules import PolicyEngine
from src.models.state import DecisionStatus
from src.models.transaction import Transaction


def test_saas_auto_approval_under_limit():
    """SaaS <= €500 with receipt should be auto-approved."""
    tx = Transaction(
        id="TEST-SAAS-1",
        amount=340.0,
        currency="EUR",
        merchant="Figma Inc.",
        category="Software",
        employee_id="EMP-1",
        receipt_url="https://receipts.corp/figma.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.is_compliant is True
    assert res.suggested_status == DecisionStatus.APPROVED
    assert len(res.violations) == 0


def test_saas_requires_review_over_limit():
    """SaaS > €500 should trigger human review."""
    tx = Transaction(
        id="TEST-SAAS-2",
        amount=750.0,
        currency="EUR",
        merchant="Tableau Software",
        category="Software",
        employee_id="EMP-1",
        receipt_url="https://receipts.corp/tableau.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.is_compliant is False
    assert res.suggested_status == DecisionStatus.PENDING_HUMAN_REVIEW
    assert "POL-SAAS-001" in res.triggered_rules


def test_client_entertainment_within_cap():
    """Entertainment <= €150/person is compliant."""
    tx = Transaction(
        id="TEST-ENT-1",
        amount=280.0,
        currency="EUR",
        merchant="Borchardt",
        category="Client Entertainment",
        employee_id="EMP-2",
        attendees_count=2,  # €140/head <= €150
        receipt_url="https://receipts.corp/borchardt.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.is_compliant is True
    assert res.suggested_status == DecisionStatus.APPROVED


def test_client_entertainment_exceeds_cap():
    """Entertainment > €150/person must require human review."""
    tx = Transaction(
        id="TEST-ENT-2",
        amount=500.0,
        currency="EUR",
        merchant="Grill Royal",
        category="Client Entertainment",
        employee_id="EMP-2",
        attendees_count=2,  # €250/head > €150
        receipt_url="https://receipts.corp/grillroyal.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.is_compliant is False
    assert res.suggested_status == DecisionStatus.PENDING_HUMAN_REVIEW
    assert "POL-ENT-002" in res.triggered_rules


def test_hard_ceiling_rule():
    """Any spend > €1500 triggers secondary human review regardless of category."""
    tx = Transaction(
        id="TEST-CAP-1",
        amount=2500.0,
        currency="EUR",
        merchant="Dell Technologies",
        category="Hardware",
        employee_id="EMP-3",
        receipt_url="https://receipts.corp/dell.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.is_compliant is False
    assert res.suggested_status == DecisionStatus.PENDING_HUMAN_REVIEW
    assert "POL-CAP-003" in res.triggered_rules


def test_missing_receipt_high_amount():
    """Missing receipt on expenses > €250 triggers strict rejection."""
    tx = Transaction(
        id="TEST-REC-1",
        amount=800.0,
        currency="EUR",
        merchant="Google Cloud",
        category="Software",
        employee_id="EMP-4",
        receipt_url=None,
    )
    res = PolicyEngine.evaluate(tx)
    assert res.suggested_status == DecisionStatus.REJECTED
    assert "POL-REC-004" in res.triggered_rules


def test_prohibited_personal_expense():
    """Prohibited luxury merchants/keywords are strictly rejected."""
    tx = Transaction(
        id="TEST-PROHIB-1",
        amount=450.0,
        currency="EUR",
        merchant="KaDeWe Berlin Luxury",
        category="Personal",
        employee_id="EMP-5",
        receipt_url="https://receipts.corp/kadewe.pdf",
    )
    res = PolicyEngine.evaluate(tx)
    assert res.suggested_status == DecisionStatus.REJECTED
    assert "POL-CAT-005" in res.triggered_rules
