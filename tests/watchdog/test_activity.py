"""Tests for ActivityState time-decay model (DESIGN.md S8.2).

Covers:
- State decay chain: ACTIVE -> READY -> IDLE
- Decision Gate awareness: WAITING_INPUT when gate pending
- Dependency blocking: BLOCKED when wu_blocked event
- No events: EXITED
- Boundary: exact threshold timing
- Mixed scenarios: gate + events
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from orchestra.persistence.decision_gate import ensure_decision_gates_table
from orchestra.persistence.schemas import EVENTS_SCHEMA_DDL
from orchestra.watchdog.activity import (
    ActivityState,
    STATE_DECAY,
    get_agent_activity_state,
)


@pytest.fixture
def events_db(tmp_path):
    """Create a temporary SQLite database with events + decision_gates tables."""
    from agno.db.sqlite import SqliteDb

    db = SqliteDb(db_file=str(tmp_path / "test_events.db"))
    with db.db_engine.connect() as conn:
        for ddl in EVENTS_SCHEMA_DDL:
            conn.execute(text(ddl))
        conn.commit()
    ensure_decision_gates_table(db)
    return db


def _insert_event(
    db,
    agent_name: str,
    event_type: str,
    created_at: datetime,
) -> None:
    """Insert a test event into the events table."""
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO events (event_type, agent_name, created_at) "
                "VALUES (:et, :an, :ca)"
            ),
            {
                "et": event_type,
                "an": agent_name,
                "ca": created_at.isoformat(),
            },
        )
        conn.commit()


def _insert_decision_gate(
    db,
    agent_id: str,
    status: str = "pending",
    workflow_run_id: str = "wf-001",
) -> None:
    """Insert a test Decision Gate."""
    from orchestra.models.decision_gate import DecisionGate, DecisionGateStatus

    gate = DecisionGate(
        id=f"dg-test-{agent_id}",
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        gate_type="plan_review",
        status=DecisionGateStatus(status),
        created_at=datetime.now(timezone.utc),
        context={},
        ttl_minutes=480,
    )
    from orchestra.persistence.decision_gate import save_decision_gate

    save_decision_gate(db, gate)


# ---------------------------------------------------------------------------
# State decay tests
# ---------------------------------------------------------------------------


class TestStateDecay:
    """Test time-based state transitions."""

    def test_active_event_within_30s_returns_active(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "tool_start", now - timedelta(seconds=10))
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.ACTIVE

    def test_active_event_after_30s_decays_to_ready(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "tool_start", now - timedelta(seconds=35))
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.READY

    def test_active_event_after_5min_decays_to_idle(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "tool_start", now - timedelta(minutes=6))
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.IDLE

    def test_ready_event_within_5min_returns_ready(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(
            events_db, "agent-a", "session_start", now - timedelta(minutes=3)
        )
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.READY

    def test_ready_event_after_5min_decays_to_idle(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(
            events_db, "agent-a", "session_start", now - timedelta(minutes=6)
        )
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.IDLE

    def test_spawn_event_is_active(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "spawn", now - timedelta(seconds=5))
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.ACTIVE

    def test_boundary_exactly_30s_stays_active(self, events_db) -> None:
        """At exactly the threshold, should NOT have decayed yet."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "tool_start", now - timedelta(seconds=30))
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        # timedelta(seconds=30) is NOT > timedelta(seconds=30), so stays ACTIVE
        assert state == ActivityState.ACTIVE

    def test_boundary_exactly_5min_stays_ready(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(
            events_db, "agent-a", "session_start", now - timedelta(minutes=5)
        )
        state = get_agent_activity_state(events_db, "agent-a", now=now)
        assert state == ActivityState.READY


# ---------------------------------------------------------------------------
# Decision Gate awareness
# ---------------------------------------------------------------------------


class TestDecisionGateAwareness:
    """WAITING_INPUT when Decision Gate is pending."""

    def test_pending_gate_returns_waiting_input(self, events_db) -> None:
        _insert_event(
            events_db,
            "agent-b",
            "tool_start",
            datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        _insert_decision_gate(events_db, "agent-b", status="pending")
        state = get_agent_activity_state(events_db, "agent-b")
        assert state == ActivityState.WAITING_INPUT

    def test_resolved_gate_does_not_block(self, events_db) -> None:
        """Approved gate should not trigger WAITING_INPUT."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-b", "tool_start", now - timedelta(seconds=5))

        # Insert a resolved gate directly
        with events_db.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO decision_gates "
                    "(id, workflow_run_id, agent_id, gate_type, status, "
                    "created_at, ttl_minutes) "
                    "VALUES (:id, :wfr, :aid, :gt, :st, :ca, :ttl)"
                ),
                {
                    "id": "dg-resolved",
                    "wfr": "wf-001",
                    "aid": "agent-b",
                    "gt": "plan_review",
                    "st": "approved",
                    "ca": now.isoformat(),
                    "ttl": 480,
                },
            )
            conn.commit()

        state = get_agent_activity_state(events_db, "agent-b", now=now)
        assert state == ActivityState.ACTIVE

    def test_gate_with_workflow_run_id(self, events_db) -> None:
        """Gate detection works with workflow_run_id parameter."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-c", "tool_start", now - timedelta(seconds=5))
        _insert_decision_gate(
            events_db, "agent-c", status="pending", workflow_run_id="wf-special"
        )
        state = get_agent_activity_state(
            events_db, "agent-c", workflow_run_id="wf-special", now=now
        )
        assert state == ActivityState.WAITING_INPUT


# ---------------------------------------------------------------------------
# Dependency blocking
# ---------------------------------------------------------------------------


class TestDependencyBlocking:
    """BLOCKED when wu_blocked event is latest."""

    def test_wu_blocked_returns_blocked(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-d", "wu_blocked", now - timedelta(seconds=10))
        state = get_agent_activity_state(events_db, "agent-d", now=now)
        assert state == ActivityState.BLOCKED

    def test_wu_unblocked_clears_blocked(self, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-d", "wu_blocked", now - timedelta(seconds=20))
        _insert_event(
            events_db, "agent-d", "wu_unblocked", now - timedelta(seconds=10)
        )
        # Most recent block-related event is unblocked, but last overall event
        # is wu_unblocked which is a READY-type event
        state = get_agent_activity_state(events_db, "agent-d", now=now)
        assert state != ActivityState.BLOCKED

    def test_gate_takes_priority_over_block(self, events_db) -> None:
        """WAITING_INPUT should take priority over BLOCKED."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-d", "wu_blocked", now - timedelta(seconds=10))
        _insert_decision_gate(events_db, "agent-d", status="pending")
        state = get_agent_activity_state(events_db, "agent-d", now=now)
        assert state == ActivityState.WAITING_INPUT


# ---------------------------------------------------------------------------
# No events / EXITED
# ---------------------------------------------------------------------------


class TestExited:
    """EXITED when no events found for agent."""

    def test_no_events_returns_exited(self, events_db) -> None:
        state = get_agent_activity_state(events_db, "nonexistent-agent")
        assert state == ActivityState.EXITED

    def test_events_for_other_agent_still_exited(self, events_db) -> None:
        _insert_event(
            events_db,
            "other-agent",
            "tool_start",
            datetime.now(timezone.utc),
        )
        state = get_agent_activity_state(events_db, "my-agent")
        assert state == ActivityState.EXITED


# ---------------------------------------------------------------------------
# State decay constants
# ---------------------------------------------------------------------------


class TestStateDecayConstants:
    """Verify decay configuration matches DESIGN.md S8.2."""

    def test_active_decays_to_ready_at_30s(self) -> None:
        next_state, threshold = STATE_DECAY[ActivityState.ACTIVE]
        assert next_state == ActivityState.READY
        assert threshold == timedelta(seconds=30)

    def test_ready_decays_to_idle_at_5min(self) -> None:
        next_state, threshold = STATE_DECAY[ActivityState.READY]
        assert next_state == ActivityState.IDLE
        assert threshold == timedelta(minutes=5)

    def test_idle_has_no_further_decay(self) -> None:
        assert ActivityState.IDLE not in STATE_DECAY

    def test_all_states_defined(self) -> None:
        assert len(ActivityState) == 6
