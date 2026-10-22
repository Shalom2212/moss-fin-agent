"""
Autonomous Spend Policy & Invoice Reconciliation Agent Orchestrator.

Orchestrates tool calling, deterministic guardrails, and HITL escalation loops.
"""

import time
from typing import Any, Dict, List, Optional

from src.agent.guardrails import (
    DeterministicGuardrailValidator,
    GuardrailViolationError,
)
from src.mcp.server import (
    verify_spend_policy,
    validate_vat_compliance,
    check_department_budget,
    trigger_human_in_the_loop_gate,
    export_to_general_ledger,
)
from src.models.state import DecisionStatus, AgentExecutionState
from src.models.transaction import (
    Transaction,
    Invoice,
    AuditDecision,
)


class SpendReconciliationAgent:
    """
    Autonomous Moss Spend Policy & Invoice Reconciliation Agent.
    
    Integrates MCP tool calls with deterministic financial guardrails:
    - Step 1: VAT compliance verification (§ 14 UStG)
    - Step 2: Corporate spend policy verification
    - Step 3: Department budget check
    - Step 4: Deterministic Guardrail enforcement
    - Step 5: HITL Gate or General Ledger Export
    """

    def __init__(self, agent_name: str = "MossReconciliationAgent"):
        self.agent_name = agent_name

    def process_transaction(self, tx: Transaction) -> AuditDecision:
        """
        Executes the end-to-end reconciliation pipeline for a single spend transaction.
        """
        start_time = time.perf_counter()
        tool_call_history: List[Dict[str, Any]] = []
        triggered_rules: List[str] = []
        explanations: List[str] = []

        try:
            # 1. MCP Tool Call: validate_vat_compliance
            inv = tx.invoice_details
            vat_result = validate_vat_compliance(
                gross_amount=tx.amount,
                vat_id=tx.vat_id or (inv.supplier_vat_id if inv else None),
                net_amount=inv.net_amount if inv else None,
                vat_amount=inv.vat_amount if inv else None,
                vat_rate=inv.vat_rate if inv else None,
                supplier_name=inv.supplier_name if inv else tx.merchant,
                invoice_number=inv.invoice_number if inv else None,
            )
            tool_call_history.append({"tool": "validate_vat_compliance", "result": vat_result})

            # 2. MCP Tool Call: verify_spend_policy
            policy_result = verify_spend_policy(
                transaction_id=tx.id,
                amount=tx.amount,
                currency=tx.currency,
                merchant=tx.merchant,
                category=tx.category,
                employee_id=tx.employee_id,
                receipt_url=tx.receipt_url,
                vat_id=tx.vat_id,
                attendees_count=tx.attendees_count or 1,
                description=tx.description,
            )
            tool_call_history.append({"tool": "verify_spend_policy", "result": policy_result})
            triggered_rules.extend(policy_result.get("triggered_rules", []))

            # 3. MCP Tool Call: check_department_budget
            budget_result = check_department_budget(
                department=tx.department or "General",
                amount=tx.amount,
                currency=tx.currency,
            )
            tool_call_history.append({"tool": "check_department_budget", "result": budget_result})

            # Initial preliminary status synthesis
            preliminary_status = DecisionStatus(policy_result.get("suggested_status", DecisionStatus.PENDING_HUMAN_REVIEW.value))
            preliminary_confidence = float(policy_result.get("confidence", 0.95))

            # Adjust confidence if VAT validation had warnings
            if not vat_result.get("is_valid", False):
                preliminary_confidence = min(preliminary_confidence, 0.85)

            # 4. Deterministic Guardrail Validation
            guardrail_check = DeterministicGuardrailValidator.apply_guardrails(
                tx=tx,
                preliminary_status=preliminary_status,
                confidence=preliminary_confidence,
                policy_violations=policy_result.get("violations", []),
                vat_valid=vat_result.get("is_valid", True),
                vat_errors=vat_result.get("errors", []),
                budget_exceeded=budget_result.get("budget_exceeded", False),
            )
            triggered_rules.extend(guardrail_check.triggered_guardrails)
            final_status = guardrail_check.final_status
            final_confidence = guardrail_check.effective_confidence

            # 5. Route to HITL Gate or Ledger Export based on Guardrails
            gl_export_record: Optional[Dict[str, Any]] = None
            hitl_gate_record: Optional[Dict[str, Any]] = None

            if final_status == DecisionStatus.PENDING_HUMAN_REVIEW:
                reason = " | ".join(guardrail_check.reasons or policy_result.get("violations", ["Flagged for manual review"]))
                hitl_gate_record = trigger_human_in_the_loop_gate(
                    transaction_id=tx.id,
                    escalation_reason=reason,
                    triggered_rules=triggered_rules,
                    confidence_score=final_confidence,
                    audit_metadata={"tool_calls": len(tool_call_history)},
                )
                tool_call_history.append({"tool": "trigger_human_in_the_loop_gate", "result": hitl_gate_record})
                explanations.append(f"HITL Gate Triggered: {reason}")

            elif final_status == DecisionStatus.REJECTED:
                rejection_reason = " | ".join(guardrail_check.reasons or policy_result.get("violations", ["Policy violation"]))
                explanations.append(f"Rejected: {rejection_reason}")

            elif final_status == DecisionStatus.APPROVED:
                # Precondition check before GL export
                DeterministicGuardrailValidator.validate_ledger_export_precondition(final_status)
                
                tax_code = "DE_19" if abs(vat_result.get("tax_rate_applied", 0.19) - 0.19) < 0.01 else "DE_7"
                gl_export_record = export_to_general_ledger(
                    transaction_id=tx.id,
                    status=final_status.value,
                    amount=tx.amount,
                    category=tx.category,
                    vat_id=tx.vat_id,
                    tax_code=tax_code,
                    currency=tx.currency,
                )
                tool_call_history.append({"tool": "export_to_general_ledger", "result": gl_export_record})
                explanations.append(
                    f"Approved: Fully compliant with spend policies & German VAT regulations (§ 14 UStG). "
                    f"Exported to General Ledger account {gl_export_record.get('gl_account')}."
                )

        except GuardrailViolationError as gve:
            final_status = DecisionStatus.PENDING_HUMAN_REVIEW
            final_confidence = 0.50
            triggered_rules.append("GUARDRAIL_VIOLATION_FATAL")
            explanations.append(f"Security Guardrail Violation: {str(gve)}")

        except Exception as e:
            # Fallback logic: Any unhandled exception fails securely into Human Review
            final_status = DecisionStatus.PENDING_HUMAN_REVIEW
            final_confidence = 0.0
            triggered_rules.append("AGENT_RUNTIME_FALLBACK")
            explanations.append(f"Autonomous agent runtime exception, routed to manual review: {str(e)}")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        unique_triggered_rules = list(dict.fromkeys(triggered_rules))
        explanation_text = " ".join(explanations)

        decision = AuditDecision.create(
            status=final_status,
            confidence_score=final_confidence,
            triggered_rules=unique_triggered_rules,
            explanation=explanation_text,
            transaction_id=tx.id,
            metadata={
                "latency_ms": round(elapsed_ms, 2),
                "tool_calls_count": len(tool_call_history),
                "agent_name": self.agent_name,
                "posted_to_gl": bool(gl_export_record and gl_export_record.get("posted", False)),
                "hitl_queued": bool(hitl_gate_record),
            },
        )
        return decision
