"""Decision Gate REST API (P0-03, DESIGN.md §4.6).

Provides two endpoints:
- POST /decision-gates          — create a new Decision Gate
- POST /decision-gates/{id}/resolve — resolve (approve/reject/override) a gate

The router requires an events_db dependency to be wired up at app startup.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agno.db.sqlite import SqliteDb

from orchestra.models.decision_gate import (
    DecisionGate,
    DecisionGateStatus,
    GateType,
)
from orchestra.persistence.decision_gate import (
    get_decision_gate,
    list_pending_gates,
    save_decision_gate,
    update_decision_gate,
)

router = APIRouter(prefix="/decision-gates", tags=["decision-gates"])

# ── Dependency injection ──
# The app must call set_events_db() at startup to wire the DB.

_events_db: SqliteDb | None = None


def set_events_db(db: SqliteDb) -> None:
    """Wire the events_db instance for API use. Called at app startup."""
    global _events_db  # noqa: PLW0603
    _events_db = db


def get_events_db() -> SqliteDb:
    """FastAPI dependency that provides the events_db instance."""
    if _events_db is None:
        raise HTTPException(
            status_code=503,
            detail="events_db not initialized. Call set_events_db() at startup.",
        )
    return _events_db


# ── Request/Response schemas ──


class CreateGateRequest(BaseModel):
    workflow_run_id: str
    agent_id: str
    gate_type: GateType
    context: dict[str, Any] = {}
    ttl_minutes: int = 480


class ResolveGateRequest(BaseModel):
    action: DecisionGateStatus
    resolver: str


class GateResponse(BaseModel):
    id: str
    workflow_run_id: str
    agent_id: str
    gate_type: str
    status: str
    created_at: str
    resolved_at: str | None
    resolver: str | None
    context: dict[str, Any]
    ttl_minutes: int


# ── Endpoints ──


@router.post("", status_code=201, response_model=GateResponse)
def create_gate(
    req: CreateGateRequest,
    db: SqliteDb = Depends(get_events_db),
) -> GateResponse:
    """Create a new Decision Gate."""
    from uuid import uuid4

    gate = DecisionGate(
        id=f"dg-{uuid4().hex[:12]}",
        workflow_run_id=req.workflow_run_id,
        agent_id=req.agent_id,
        gate_type=req.gate_type,
        created_at=datetime.now(tz=timezone.utc),
        context=req.context,
        ttl_minutes=req.ttl_minutes,
    )
    save_decision_gate(db, gate)
    return _gate_to_response(gate)


@router.post("/{gate_id}/resolve", response_model=GateResponse)
def resolve_gate(
    gate_id: str,
    req: ResolveGateRequest,
    db: SqliteDb = Depends(get_events_db),
) -> GateResponse:
    """Resolve a pending Decision Gate (approve/reject/override)."""
    if req.action not in (
        DecisionGateStatus.APPROVED,
        DecisionGateStatus.REJECTED,
        DecisionGateStatus.OVERRIDE,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolve action: {req.action}. "
            "Must be 'approved', 'rejected', or 'override'.",
        )

    gate = get_decision_gate(db, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate '{gate_id}' not found")
    if gate.status != DecisionGateStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Gate '{gate_id}' is already {gate.status.value}, cannot resolve",
        )

    gate.status = req.action
    gate.resolved_at = datetime.now(tz=timezone.utc)
    gate.resolver = req.resolver
    update_decision_gate(db, gate)
    return _gate_to_response(gate)


@router.get("", response_model=list[GateResponse])
def list_gates(
    agent_id: str | None = None,
    workflow_run_id: str | None = None,
    db: SqliteDb = Depends(get_events_db),
) -> list[GateResponse]:
    """List pending Decision Gates, optionally filtered."""
    gates = list_pending_gates(db, agent_id=agent_id, workflow_run_id=workflow_run_id)
    return [_gate_to_response(g) for g in gates]


def _gate_to_response(gate: DecisionGate) -> GateResponse:
    return GateResponse(
        id=gate.id,
        workflow_run_id=gate.workflow_run_id,
        agent_id=gate.agent_id,
        gate_type=gate.gate_type,
        status=gate.status.value,
        created_at=gate.created_at.isoformat(),
        resolved_at=gate.resolved_at.isoformat() if gate.resolved_at else None,
        resolver=gate.resolver,
        context=gate.context,
        ttl_minutes=gate.ttl_minutes,
    )
