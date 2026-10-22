"""
Model Context Protocol (MCP) Server exposing European FinOps tools for Moss Spend Management.

Tools Exposed:
1. verify_spend_policy: Deterministic corporate spend limits & category caps
2. validate_vat_compliance: German § 14 UStG VAT compliance & UID format verification
3. check_department_budget: Real-time department budget check against limits
4. trigger_human_in_the_loop_gate: Human-in-the-Loop escalation gate
5. export_to_general_ledger: General Ledger (GL) posting (SKR03/SKR04) guarded by strict status
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        # Fallback minimal mock server if mcp is unavailable
        class MCPServer:
            def __init__(self, name: str):
                self.name = name
                self.tools = {}

            def tool(self, **kwargs):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return decorator

from src.config import config
from src.engine.policy_rules import PolicyEngine
from src.engine.vat_validator import GermanVATValidator
from src.models.state import DecisionStatus
from src.models.transaction import Transaction, Invoice, compute_audit_hash

# Initialize MCP Server instance
mcp_server = MCPServer("moss-fin-agent")


# In-memory mock department budget tracking
DEPARTMENT_SPENT: Dict[str, float] = {
    "Engineering": 15000.0,
    "Marketing": 38000.0,  # Near €40,000 budget cap
    "Sales": 8000.0,
    "Operations": 4500.0,
}


@mcp_server.tool()
def verify_spend_policy(
    transaction_id: str,
    amount: float,
    currency: str,
    merchant: str,
    category: str,
    employee_id: str,
    receipt_url: Optional[str] = None,
    vat_id: Optional[str] = None,
    attendees_count: int = 1,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluates corporate spend policies for an expense transaction.
    
    Financial semantics:
    - SaaS/Software: Auto-approved if amount <= €500.00 and receipt is provided.
    - Client Entertainment: Maximum €150.00 per attendee. Flags review if exceeded.
    - Hard Ceiling: Any transaction > €1,500.00 strictly requires human review regardless of category.
    - Receipts: Mandatory for any transaction > €250.00.
    
    Args:
        transaction_id: Unique spend identifier
        amount: Gross expense amount
        currency: Currency code (e.g. 'EUR')
        merchant: Supplier or vendor name
        category: Expense category ('Software', 'Client Entertainment', 'Hardware', etc.)
        employee_id: Submitting employee
        receipt_url: URI to receipt documentation
        vat_id: Merchant VAT ID if available
        attendees_count: Count of dinner/event attendees
        description: Business justification memo
        
    Returns:
        Dictionary containing is_compliant, suggested_status, confidence, violations, and triggered_rules.
    """
    tx = Transaction(
        id=transaction_id,
        amount=amount,
        currency=currency,
        merchant=merchant,
        category=category,
        employee_id=employee_id,
        receipt_url=receipt_url,
        vat_id=vat_id,
        attendees_count=attendees_count,
        description=description,
    )
    result = PolicyEngine.evaluate(tx)
    return {
        "transaction_id": transaction_id,
        "is_compliant": result.is_compliant,
        "suggested_status": result.suggested_status.value,
        "confidence": result.confidence,
        "violations": result.violations,
        "triggered_rules": result.triggered_rules,
        "requires_human_review": result.requires_human_review,
        "is_rejected": result.is_rejected,
        "details": result.details,
    }


@mcp_server.tool()
def validate_vat_compliance(
    gross_amount: float,
    vat_id: Optional[str] = None,
    net_amount: Optional[float] = None,
    vat_amount: Optional[float] = None,
    vat_rate: Optional[float] = None,
    supplier_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validates German & EU VAT tax compliance under § 14 UStG and § 33 UStDV.
    
    Financial semantics:
    - German VAT IDs must match format DE + 9 digits (DE[0-9]{9}).
    - § 14 Abs. 4 Nr. 7/8 UStG math check: Net Amount + VAT Amount must equal Gross Amount.
    - Permitted statutory rates: 19% (Standard), 7% (Reduced - e.g. books, rail), 0% (Reverse Charge).
    - Invoices <= €250.00 (Kleinbetragsrechnungen) are exempt from supplier VAT ID mandate.
    - Invoices > €250.00 without a valid VAT ID or with tax mismatch fail compliance.
    
    Args:
        gross_amount: Gross invoice amount in EUR
        vat_id: Supplier VAT ID (e.g. DE123456789)
        net_amount: Net amount before tax
        vat_amount: VAT tax charged
        vat_rate: Tax rate percentage (e.g. 0.19 or 19.0)
        supplier_name: Legal name of merchant
        invoice_number: Invoice identifier
        
    Returns:
        Dictionary with is_valid, math_consistent, vat_id_valid, errors, and Kleinbetragsrechnung status.
    """
    inv = None
    if net_amount is not None and vat_amount is not None and vat_rate is not None:
        inv = Invoice(
            gross_amount=gross_amount,
            net_amount=net_amount,
            vat_amount=vat_amount,
            vat_rate=vat_rate,
            supplier_vat_id=vat_id,
            supplier_name=supplier_name,
            invoice_number=invoice_number,
        )

    result = GermanVATValidator.validate_invoice(
        gross_amount=gross_amount,
        net_amount=net_amount,
        vat_amount=vat_amount,
        vat_rate=vat_rate,
        vat_id=vat_id,
        invoice=inv,
    )

    return {
        "is_valid": result.is_valid,
        "is_kleinbetragsrechnung": result.is_kleinbetragsrechnung,
        "tax_rate_applied": result.tax_rate_applied,
        "vat_id_valid": result.vat_id_valid,
        "math_consistent": result.math_consistent,
        "errors": result.errors,
        "details": result.details,
    }


@mcp_server.tool()
def check_department_budget(
    department: str,
    amount: float,
    currency: str = "EUR",
) -> Dict[str, Any]:
    """
    Checks if a spend transaction exceeds the department's remaining budget.
    
    Financial semantics:
    - Checks allocated department quarterly spend limits.
    - If total spent + amount > allocated limit, flags budget overrun for secondary approval.
    
    Args:
        department: Cost center / department name (e.g. 'Engineering', 'Marketing')
        amount: Transaction gross amount
        currency: Currency code
        
    Returns:
        Dictionary with budget_exceeded, remaining_budget, total_budget, and current_spent.
    """
    dept = department if department in config.DEFAULT_DEPARTMENT_BUDGETS else "General"
    total_budget = config.DEFAULT_DEPARTMENT_BUDGETS.get(dept, 25000.0)
    current_spent = DEPARTMENT_SPENT.get(dept, 0.0)
    projected_spent = current_spent + amount
    budget_exceeded = projected_spent > total_budget
    remaining_before = max(0.0, total_budget - current_spent)
    remaining_after = max(0.0, total_budget - projected_spent)

    return {
        "department": dept,
        "transaction_amount": amount,
        "currency": currency,
        "total_budget": total_budget,
        "current_spent": current_spent,
        "remaining_budget": remaining_after,
        "budget_exceeded": budget_exceeded,
        "overrun_amount": max(0.0, projected_spent - total_budget) if budget_exceeded else 0.0,
    }


@mcp_server.tool()
def trigger_human_in_the_loop_gate(
    transaction_id: str,
    escalation_reason: str,
    triggered_rules: List[str],
    confidence_score: float,
    audit_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Enforces a Human-in-the-Loop (HITL) review gate for high-risk or uncertain transactions.
    
    Financial semantics:
    - Queues transaction for FinOps Controller or Department Head manual review.
    - Freezes automatic payment & ledger release.
    - Generates a cryptographic audit trail hash to log escalation immutably.
    
    Args:
        transaction_id: Identifier of transaction under review
        escalation_reason: Specific policy reason or uncertainty explanation
        triggered_rules: List of rule IDs requiring human approval
        confidence_score: Current model/agent confidence score (< 0.90 or policy trigger)
        audit_metadata: Supplemental diagnostic context
        
    Returns:
        Dictionary containing gate_status, escalation_id, audit_hash, and queued_at.
    """
    now = datetime.now(timezone.utc).isoformat()
    meta = audit_metadata or {}
    escalation_payload = {
        "transaction_id": transaction_id,
        "gate": "HUMAN_IN_THE_LOOP",
        "reason": escalation_reason,
        "rules": triggered_rules,
        "confidence": confidence_score,
        "queued_at": now,
        "metadata": meta,
    }
    audit_hash = compute_audit_hash(escalation_payload)

    return {
        "gate_status": "QUEUED_FOR_HUMAN_REVIEW",
        "status": DecisionStatus.PENDING_HUMAN_REVIEW.value,
        "transaction_id": transaction_id,
        "escalation_reason": escalation_reason,
        "triggered_rules": triggered_rules,
        "confidence_score": confidence_score,
        "audit_trail_hash": audit_hash,
        "queued_at": now,
    }


@mcp_server.tool()
def export_to_general_ledger(
    transaction_id: str,
    status: str,
    amount: float,
    category: str,
    vat_id: Optional[str] = None,
    tax_code: str = "DE_19",
    currency: str = "EUR",
    audit_trail_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Exports fully reconciled transaction to General Ledger (DATEV / SAP / Moss ERP).
    
    Financial semantics:
    - STRICT GUARD: Only executes if status is strictly 'APPROVED'.
    - If status is PENDING_HUMAN_REVIEW or REJECTED, export is blocked with a security violation.
    - Assigns German Standard Chart of Accounts (SKR03 / SKR04) account code.
    
    Args:
        transaction_id: Transaction identifier
        status: Current decision status ('APPROVED', 'REJECTED', 'PENDING_HUMAN_REVIEW')
        amount: Gross amount to book
        category: Expense category for account mapping
        vat_id: Merchant VAT ID
        tax_code: German tax booking code (e.g. DE_19, DE_7)
        currency: Currency code
        audit_trail_hash: Cryptographic audit hash from audit decision
        
    Returns:
        Ledger booking confirmation record or rejection reason.
    """
    # Strict deterministic gate: only APPROVED status can export to GL
    if status != DecisionStatus.APPROVED.value:
        return {
            "success": False,
            "error": "GL_EXPORT_BLOCKED",
            "message": f"Security Guardrail: Cannot export transaction '{transaction_id}' with status '{status}'. Only strictly APPROVED transactions may be posted to the General Ledger.",
            "transaction_id": transaction_id,
            "posted": False,
        }

    account_code = config.GL_ACCOUNT_MAPPING.get(category, "4900")
    posted_timestamp = datetime.now(timezone.utc).isoformat()
    journal_id = f"JRN-{datetime.now().strftime('%Y%m%d')}-{transaction_id[:8]}"

    posting_record = {
        "transaction_id": transaction_id,
        "journal_id": journal_id,
        "gl_account": account_code,
        "category": category,
        "amount": amount,
        "currency": currency,
        "vat_id": vat_id,
        "tax_code": tax_code,
        "posted_at": posted_timestamp,
        "audit_trail_hash": audit_trail_hash,
    }
    posting_hash = compute_audit_hash(posting_record)

    return {
        "success": True,
        "posted": True,
        "journal_id": journal_id,
        "gl_account": account_code,
        "transaction_id": transaction_id,
        "posting_hash": posting_hash,
        "message": f"Successfully exported to General Ledger account {account_code}",
        "posted_at": posted_timestamp,
    }


def create_mcp_app():
    """Factory function to expose the MCP server ASGI or runner."""
    return mcp_server


if __name__ == "__main__":
    # If run directly via command line, run stdio server
    mcp_server.run()
