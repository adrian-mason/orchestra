"""Decision Gate workflow operations (DESIGN.md §4.6, AC-04).

Provides the Workflow integration layer for Decision Gates, built on top
of the persistence CRUD layer (orchestra.persistence.decision_gate):

- initialize_decision_gates_schema(): DDL setup via persistence layer
- create_decision_gate(): factory that constructs + persists a new PENDING gate
- has_pending_decision_gate(): query for Watchdog/ActivityState
- resolve_decision_gate(): validates + transitions gate to APPROVED/REJECTED/OVERRIDE
- reap_expired_gates(): marks TTL-expired gates as EXPIRED

Business logic (validation, ID generation, TTL reaping) lives here.
Raw SQL lives in the persistence layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from orchestra.models.decision_gate import DecisionGate, DecisionGateStatus
from orchestra.persistence.decision_gate import (
    DECISION_GATES_SCHEMA_DDL,
    ensure_decision_gates_table,
    find_expired_gates,
    get_decision_gate,
    has_pending_decision_gate,
    list_pending_gates,
    save_decision_gate,
    update_decision_gate,
)

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb

# Re-export for backward compatibility and convenience
__all__ = [
    "DECISION_GATES_SCHEMA_DDL",
    "create_decision_gate",
    "get_decision_gate",
    "get_pending_gates",
    "has_pending_decision_gate",
    "initialize_decision_gates_schema",
    "reap_expired_gates",
    "resolve_decision_gate",
]


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

def initialize_decision_gates_schema(events_db: SqliteDb) -> None:
    """Apply decision_gates DDL to events_db.

    Delegates to the persistence layer's ensure_decision_gates_table().
    All DDL is idempotent (IF NOT EXISTS).
    """
    ensure_decision_gates_table(events_db)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_decision_gate(
    events_db: SqliteDb,
    *,
    workflow_run_id: str,
    agent_id: str,
    gate_type: str,
    context: dict | None = None,
    ttl_minutes: int = 480,
) -> DecisionGate:
    """Create and persist a new PENDING Decision Gate.

    Args:
        events_db: The events database (SqliteDb instance).
        workflow_run_id: ID of the workflow run being gated.
        agent_id: ID of the agent blocked by this gate.
        gate_type: One of plan_review, design_review, integration,
            pr_review, pr_merge.
        context: Escalation context (blocker list, round history, etc.).
        ttl_minutes: Minutes before auto-expiry. Default 480 (8 hours).

    Returns:
        The persisted DecisionGate instance.

    Raises:
        ValidationError: If gate_type is not a valid GateType literal.
    """
    gate = DecisionGate(
        id=f"dg-{uuid.uuid4().hex[:12]}",
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        gate_type=gate_type,  # type: ignore[arg-type]
        status=DecisionGateStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        context=context or {},
        ttl_minutes=ttl_minutes,
    )
    save_decision_gate(events_db, gate)
    return gate


# ---------------------------------------------------------------------------
# Queries (re-exported from persistence with workflow-level docstrings)
# ---------------------------------------------------------------------------

# has_pending_decision_gate and get_decision_gate are re-exported directly
# from the persistence layer (see imports above). They are available as:
#   from orchestra.workflow.gate import has_pending_decision_gate
#   from orchestra.workflow.gate import get_decision_gate


def get_pending_gates(
    events_db: SqliteDb,
    agent_id: str | None = None,
) -> list[DecisionGate]:
    """Fetch all PENDING gates, optionally filtered by agent_id.

    Thin wrapper over persistence.list_pending_gates for workflow callers.
    """
    return list_pending_gates(events_db, agent_id=agent_id)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_decision_gate(
    events_db: SqliteDb,
    gate_id: str,
    *,
    action: DecisionGateStatus,
    resolver: str,
) -> DecisionGate:
    """Resolve a PENDING gate to APPROVED, REJECTED, or OVERRIDE.

    Args:
        events_db: The events database.
        gate_id: ID of the gate to resolve.
        action: Target status (must be APPROVED, REJECTED, or OVERRIDE).
        resolver: Human approver identifier.

    Returns:
        The updated DecisionGate.

    Raises:
        ValueError: If gate not found, already resolved, or invalid action.
    """
    if action not in (
        DecisionGateStatus.APPROVED,
        DecisionGateStatus.REJECTED,
        DecisionGateStatus.OVERRIDE,
    ):
        raise ValueError(
            f"Invalid resolution action: {action!r}. "
            f"Must be APPROVED, REJECTED, or OVERRIDE."
        )

    gate = get_decision_gate(events_db, gate_id)
    if gate is None:
        raise ValueError(f"Decision gate '{gate_id}' not found")
    if gate.status != DecisionGateStatus.PENDING:
        raise ValueError(
            f"Decision gate '{gate_id}' is already {gate.status.value}, "
            f"cannot resolve"
        )

    gate.status = action
    gate.resolved_at = datetime.now(timezone.utc)
    gate.resolver = resolver
    update_decision_gate(events_db, gate)
    return gate


# ---------------------------------------------------------------------------
# TTL Reaper
# ---------------------------------------------------------------------------

def reap_expired_gates(events_db: SqliteDb) -> list[DecisionGate]:
    """Find and expire all PENDING gates past their TTL.

    Called by Watchdog's monitor_loop each cycle. Gates whose
    created_at + ttl_minutes < now are marked EXPIRED.

    Returns:
        List of gates that were expired in this sweep.
    """
    expired_gates = find_expired_gates(events_db)
    now = datetime.now(timezone.utc)

    for gate in expired_gates:
        gate.status = DecisionGateStatus.EXPIRED
        gate.resolved_at = now
        update_decision_gate(events_db, gate)

    return expired_gates
