"""Decision Gate model (AC-04, DESIGN.md §4.6).

A Decision Gate is a persistable, observable protocol object that connects
Review Gate human escalation, Watchdog gate awareness, and REST API
approval/rejection.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DecisionGateStatus(str, Enum):
    """Decision Gate lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    OVERRIDE = "override"


GateType = Literal[
    "plan_review",
    "design_review",
    "integration",
    "pr_review",
    "pr_merge",
]


class DecisionGate(BaseModel):
    """Persistable Decision Gate protocol object.

    Stored in the ``decision_gates`` table of events_db.
    """

    id: str  # dg-{uuid}
    workflow_run_id: str
    agent_id: str
    gate_type: GateType
    status: DecisionGateStatus = DecisionGateStatus.PENDING
    created_at: datetime
    resolved_at: datetime | None = None
    resolver: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    ttl_minutes: int = 480  # 8 hours

    def to_row(self) -> dict[str, Any]:
        """Serialize to a dict suitable for SQLite insertion."""
        return {
            "id": self.id,
            "workflow_run_id": self.workflow_run_id,
            "agent_id": self.agent_id,
            "gate_type": self.gate_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolver": self.resolver,
            "context": json.dumps(self.context),
            "ttl_minutes": self.ttl_minutes,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> DecisionGate:
        """Deserialize from a SQLite row dict."""
        data = dict(row)
        if isinstance(data.get("context"), str):
            data["context"] = json.loads(data["context"])
        return cls.model_validate(data)
