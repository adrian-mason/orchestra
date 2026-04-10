"""Activity State time-decay model for agent health monitoring.

DESIGN.md S8.2: 6 states with time-based decay, used by Watchdog
monitor_loop to determine agent health and escalation decisions.

States:
    ACTIVE -> (30s decay) -> READY -> (5min decay) -> IDLE
    WAITING_INPUT: Decision Gate pending (no escalation)
    BLOCKED: Dependency blocked (no escalation, log only)
    EXITED: Process exited (immediate termination)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from orchestra.persistence.decision_gate import has_pending_decision_gate

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb


class ActivityState(Enum):
    """Agent activity states with time-decay semantics."""

    ACTIVE = "active"
    READY = "ready"
    IDLE = "idle"
    WAITING_INPUT = "waiting_input"
    BLOCKED = "blocked"
    EXITED = "exited"


# Time-decay rules: state -> (next_state, threshold)
# ACTIVE decays to READY after 30s of no new events
# READY decays to IDLE after 5min of no new events
STATE_DECAY: dict[ActivityState, tuple[ActivityState, timedelta]] = {
    ActivityState.ACTIVE: (ActivityState.READY, timedelta(seconds=30)),
    ActivityState.READY: (ActivityState.IDLE, timedelta(minutes=5)),
}

# Event types that indicate active execution
_ACTIVE_EVENT_TYPES = frozenset({"tool_start", "spawn"})


def get_agent_activity_state(
    events_db: SqliteDb,
    agent_id: str,
    workflow_run_id: str | None = None,
    *,
    now: datetime | None = None,
) -> ActivityState:
    """Derive agent activity state from events and Decision Gate status.

    Implements DESIGN.md S8.2 time-decay model:
    1. If agent has pending Decision Gate -> WAITING_INPUT
    2. If agent is blocked on DAG dependency -> BLOCKED
    3. Otherwise, derive from last event + time decay

    Args:
        events_db: The events database.
        agent_id: Agent identifier.
        workflow_run_id: Optional workflow run ID for gate-level detection.
        now: Override current time (for testing).

    Returns:
        Current ActivityState after decay rules applied.
    """
    now = now or datetime.now(timezone.utc)

    # Priority 1: Decision Gate pending -> WAITING_INPUT
    if has_pending_decision_gate(events_db, agent_id, workflow_run_id=workflow_run_id):
        return ActivityState.WAITING_INPUT

    # Priority 2: Blocked on DAG dependency
    if _is_blocked_on_dependency(events_db, agent_id):
        return ActivityState.BLOCKED

    # Priority 3: Derive from last event + time decay
    last_event = _get_last_event(events_db, agent_id)
    if last_event is None:
        return ActivityState.EXITED

    event_time = _parse_datetime(last_event["created_at"])
    elapsed = now - event_time

    # Determine base state from event type
    event_type = last_event["event_type"]
    base_state = (
        ActivityState.ACTIVE
        if event_type in _ACTIVE_EVENT_TYPES
        else ActivityState.READY
    )

    # Apply time decay chain
    current = base_state
    while current in STATE_DECAY:
        next_state, threshold = STATE_DECAY[current]
        if elapsed > threshold:
            current = next_state
        else:
            break

    return current


def _is_blocked_on_dependency(events_db: SqliteDb, agent_id: str) -> bool:
    """Check if agent is waiting on DAG predecessor WorkUnits.

    Uses wu_blocked/wu_unblocked event protocol (DESIGN.md S8.2).
    """
    from sqlalchemy import text

    with events_db.db_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT event_type FROM events "
                "WHERE agent_name = :agent_id "
                "AND event_type IN ('wu_blocked', 'wu_unblocked') "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"agent_id": agent_id},
        )
        row = result.mappings().fetchone()
        return row is not None and row["event_type"] == "wu_blocked"


def _get_last_event(events_db: SqliteDb, agent_id: str) -> dict[str, Any] | None:
    """Fetch the most recent event for an agent."""
    from sqlalchemy import text

    with events_db.db_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT event_type, created_at FROM events "
                "WHERE agent_name = :agent_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"agent_id": agent_id},
        )
        row = result.mappings().fetchone()
        return dict(row) if row is not None else None


def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string to timezone-aware datetime."""
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
