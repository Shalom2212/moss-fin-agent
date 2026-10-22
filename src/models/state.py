"""
State and lifecycle definitions for moss-fin-agent.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DecisionStatus(str, Enum):
    """Core final and intermediate decision statuses."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


class SeverityLevel(str, Enum):
    """Rule violation severity."""
    INFO = "INFO"
    WARNING = "WARNING"
    REVIEW = "REVIEW"
    HARD_STOP = "HARD_STOP"
    REJECT = "REJECT"


class AgentExecutionState(BaseModel):
    """State tracking object for agent orchestration loops."""
    transaction_id: str
    status: DecisionStatus = DecisionStatus.PENDING_HUMAN_REVIEW
    confidence_score: float = 1.0
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    triggered_rules: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    audit_trail_hash: Optional[str] = None
    human_escalation_reason: Optional[str] = None
    ledger_posted: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
