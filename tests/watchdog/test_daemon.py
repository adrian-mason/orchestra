"""Tests for WatchdogDaemon Tier 0 (DESIGN.md S7.2).

Covers acceptance criteria:
- 30-second interval configuration
- IDLE escalation: level 1 -> warn, level 2 -> nudge
- Decision Gate awareness (skip WAITING_INPUT agents)
- Calls reap_expired_gates() each cycle
- Does NOT implement Tier 1/2

Failure paths:
- Agent exits (no events)
- Dependency blocked
- Multiple agents with mixed states
- Escalation counter reset on recovery
- Nudge callback invocation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from orchestra.models.decision_gate import DecisionGate, DecisionGateStatus
from orchestra.persistence.decision_gate import (
    ensure_decision_gates_table,
    save_decision_gate,
)
from orchestra.persistence.schemas import EVENTS_SCHEMA_DDL
from orchestra.watchdog.daemon import WatchdogDaemon


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


@pytest.fixture
def daemon(events_db):
    """Create a WatchdogDaemon with test configuration."""
    return WatchdogDaemon(events_db=events_db, check_interval_sec=30)


def _insert_event(db, agent_name: str, event_type: str, created_at: datetime) -> None:
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO events (event_type, agent_name, created_at) "
                "VALUES (:et, :an, :ca)"
            ),
            {"et": event_type, "an": agent_name, "ca": created_at.isoformat()},
        )
        conn.commit()


def _insert_pending_gate(
    db, agent_id: str, ttl_minutes: int = 480, created_at: datetime | None = None
) -> str:
    gate = DecisionGate(
        id=f"dg-{agent_id}",
        workflow_run_id="wf-001",
        agent_id=agent_id,
        gate_type="plan_review",
        status=DecisionGateStatus.PENDING,
        created_at=created_at or datetime.now(timezone.utc),
        context={},
        ttl_minutes=ttl_minutes,
    )
    save_decision_gate(db, gate)
    return gate.id


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_default_interval_30s(self, events_db) -> None:
        d = WatchdogDaemon(events_db=events_db)
        assert d.check_interval_sec == 30

    def test_custom_interval(self, events_db) -> None:
        d = WatchdogDaemon(events_db=events_db, check_interval_sec=60)
        assert d.check_interval_sec == 60

    def test_register_agent(self, daemon) -> None:
        daemon.register_agent("a1", "Agent One")
        assert "a1" in daemon.monitored_agents

    def test_unregister_agent(self, daemon) -> None:
        daemon.register_agent("a1", "Agent One")
        daemon.unregister_agent("a1")
        assert "a1" not in daemon.monitored_agents

    def test_unregister_nonexistent_agent_is_noop(self, daemon) -> None:
        daemon.unregister_agent("nonexistent")  # no error

    def test_monitored_agents_returns_copy(self, daemon) -> None:
        daemon.register_agent("a1", "Agent One")
        agents = daemon.monitored_agents
        agents.clear()
        assert "a1" in daemon.monitored_agents


# ---------------------------------------------------------------------------
# IDLE escalation
# ---------------------------------------------------------------------------


class TestIdleEscalation:
    """IDLE level 1 -> warn, level 2 -> nudge."""

    def test_idle_level_1_warns(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("agent-a", "Agent A")

        actions = daemon.run_once()
        idle_action = [a for a in actions if a["agent_id"] == "agent-a"][0]

        assert idle_action["state"] == "idle"
        assert idle_action["action"] == "warn"
        assert idle_action["escalation_level"] == 1

    def test_idle_level_2_nudges(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("agent-a", "Agent A")

        # First cycle: level 1 (warn)
        daemon.run_once()
        # Second cycle: level 2 (nudge)
        actions = daemon.run_once()
        idle_action = [a for a in actions if a["agent_id"] == "agent-a"][0]

        assert idle_action["action"] == "nudge"
        assert idle_action["escalation_level"] == 2

    def test_idle_level_3_deferred(self, daemon, events_db) -> None:
        """Level 3+ logs critical but defers AI triage to P2-10."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("agent-a", "Agent A")

        # 3 cycles
        daemon.run_once()
        daemon.run_once()
        actions = daemon.run_once()
        idle_action = [a for a in actions if a["agent_id"] == "agent-a"][0]

        assert idle_action["action"] == "stuck_deferred"
        assert idle_action["escalation_level"] == 3

    def test_nudge_callback_invoked(self, daemon, events_db) -> None:
        nudged = []
        daemon.set_on_nudge(lambda aid, name: nudged.append((aid, name)))

        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("agent-a", "Agent A")

        daemon.run_once()  # level 1
        assert len(nudged) == 0
        daemon.run_once()  # level 2 -> nudge
        assert nudged == [("agent-a", "Agent A")]


# ---------------------------------------------------------------------------
# Escalation reset
# ---------------------------------------------------------------------------


class TestEscalationReset:
    def test_active_agent_resets_escalation(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-a", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("agent-a", "Agent A")

        # Escalate to level 1
        daemon.run_once()
        assert daemon.monitored_agents["agent-a"].escalation_level == 1

        # Agent becomes active again
        _insert_event(events_db, "agent-a", "tool_start", datetime.now(timezone.utc))
        actions = daemon.run_once()
        reset_action = [a for a in actions if a["agent_id"] == "agent-a"][0]

        assert reset_action["action"] == "reset"
        assert daemon.monitored_agents["agent-a"].escalation_level == 0


# ---------------------------------------------------------------------------
# Decision Gate awareness
# ---------------------------------------------------------------------------


class TestGateAwareness:
    def test_waiting_input_skips_escalation(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-b", "session_start", now - timedelta(minutes=10))
        _insert_pending_gate(events_db, "agent-b")
        daemon.register_agent("agent-b", "Agent B")

        actions = daemon.run_once()
        gate_action = [a for a in actions if a["agent_id"] == "agent-b"][0]

        assert gate_action["state"] == "waiting_input"
        assert gate_action["action"] == "skip_gate"
        # Escalation level should NOT increase
        assert daemon.monitored_agents["agent-b"].escalation_level == 0

    def test_gate_skip_does_not_increment_escalation(
        self, daemon, events_db
    ) -> None:
        """Multiple cycles with gate pending should never escalate."""
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-b", "session_start", now - timedelta(minutes=10))
        _insert_pending_gate(events_db, "agent-b")
        daemon.register_agent("agent-b", "Agent B")

        for _ in range(5):
            daemon.run_once()
        assert daemon.monitored_agents["agent-b"].escalation_level == 0


# ---------------------------------------------------------------------------
# Expired gate reaping
# ---------------------------------------------------------------------------


class TestExpiredGateReaping:
    def test_reaps_expired_gates_each_cycle(self, daemon, events_db) -> None:
        # Create a gate that's already expired (created 10 hours ago, TTL 1 minute)
        _insert_pending_gate(
            events_db,
            "agent-c",
            ttl_minutes=1,
            created_at=datetime.now(timezone.utc) - timedelta(hours=10),
        )
        daemon.register_agent("agent-c", "Agent C")
        _insert_event(
            events_db, "agent-c", "tool_start", datetime.now(timezone.utc)
        )

        daemon.run_once()

        # Gate should now be expired
        from orchestra.persistence.decision_gate import get_decision_gate

        gate = get_decision_gate(events_db, "dg-agent-c")
        assert gate is not None
        assert gate.status == DecisionGateStatus.EXPIRED


# ---------------------------------------------------------------------------
# Agent exit
# ---------------------------------------------------------------------------


class TestAgentExit:
    """Tests for genuinely exited agents (past startup grace period)."""

    def _register_past_grace(self, daemon, agent_id: str, agent_name: str) -> None:
        """Register an agent and backdate registered_at past the grace period."""
        daemon.register_agent(agent_id, agent_name)
        daemon._agents[agent_id].registered_at = (
            datetime.now(timezone.utc) - timedelta(seconds=daemon.check_interval_sec + 1)
        )

    def test_exited_agent_triggers_callback(self, daemon, events_db) -> None:
        exited = []
        daemon.set_on_agent_exited(lambda aid, name: exited.append((aid, name)))
        self._register_past_grace(daemon, "ghost", "Ghost Agent")

        actions = daemon.run_once()
        exit_action = [a for a in actions if a["agent_id"] == "ghost"][0]

        assert exit_action["state"] == "exited"
        assert exit_action["action"] == "exited"
        assert exited == [("ghost", "Ghost Agent")]

    def test_exited_without_callback_no_error(self, daemon) -> None:
        self._register_past_grace(daemon, "ghost", "Ghost Agent")
        actions = daemon.run_once()
        assert actions[0]["action"] == "exited"

    def test_exited_agent_auto_unregistered(self, daemon) -> None:
        """EXITED agents are auto-unregistered to prevent log spam."""
        self._register_past_grace(daemon, "ghost", "Ghost Agent")
        daemon.run_once()
        assert "ghost" not in daemon.monitored_agents

    def test_exited_agent_not_logged_on_subsequent_cycles(self, daemon) -> None:
        """After auto-unregister, exited agent produces no further actions."""
        self._register_past_grace(daemon, "ghost", "Ghost Agent")
        daemon.run_once()
        actions = daemon.run_once()
        assert len(actions) == 0


# ---------------------------------------------------------------------------
# Startup grace period
# ---------------------------------------------------------------------------


class TestStartupGrace:
    """Newly registered agents with no events get grace period, not EXITED."""

    def test_new_agent_no_events_gets_grace(self, daemon) -> None:
        """Agent registered just now with no events → startup_grace, not exited."""
        daemon.register_agent("new-agent", "New Agent")
        actions = daemon.run_once()
        action = actions[0]

        assert action["state"] == "ready"
        assert action["action"] == "startup_grace"
        assert "new-agent" in daemon.monitored_agents

    def test_grace_expires_then_exited(self, daemon) -> None:
        """After grace period, agent with no events → exited."""
        daemon.register_agent("new-agent", "New Agent")
        # Backdate past grace
        daemon._agents["new-agent"].registered_at = (
            datetime.now(timezone.utc) - timedelta(seconds=daemon.check_interval_sec + 1)
        )
        actions = daemon.run_once()
        assert actions[0]["action"] == "exited"
        assert "new-agent" not in daemon.monitored_agents

    def test_agent_emits_event_during_grace_becomes_active(self, daemon, events_db) -> None:
        """Agent that emits an event during grace → normal state, not exited."""
        daemon.register_agent("new-agent", "New Agent")
        _insert_event(events_db, "new-agent", "tool_start", datetime.now(timezone.utc))
        actions = daemon.run_once()
        assert actions[0]["action"] == "reset"
        assert actions[0]["state"] == "active"


# ---------------------------------------------------------------------------
# Blocked agents
# ---------------------------------------------------------------------------


class TestBlockedAgents:
    def test_blocked_agent_logged_not_escalated(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)
        _insert_event(events_db, "agent-d", "wu_blocked", now - timedelta(seconds=10))
        daemon.register_agent("agent-d", "Agent D")

        actions = daemon.run_once()
        blocked_action = [a for a in actions if a["agent_id"] == "agent-d"][0]

        assert blocked_action["state"] == "blocked"
        assert blocked_action["action"] == "log_blocked"
        assert daemon.monitored_agents["agent-d"].escalation_level == 0


# ---------------------------------------------------------------------------
# Multi-agent scenarios
# ---------------------------------------------------------------------------


class TestMultiAgent:
    def test_mixed_states(self, daemon, events_db) -> None:
        now = datetime.now(timezone.utc)

        # Agent A: active
        _insert_event(events_db, "a", "tool_start", now - timedelta(seconds=5))
        daemon.register_agent("a", "Active")

        # Agent B: idle
        _insert_event(events_db, "b", "session_start", now - timedelta(minutes=10))
        daemon.register_agent("b", "Idle")

        # Agent C: waiting (gate)
        _insert_event(events_db, "c", "tool_start", now - timedelta(minutes=10))
        _insert_pending_gate(events_db, "c")
        daemon.register_agent("c", "Gated")

        # Agent D: exited (backdate past grace period)
        daemon.register_agent("d", "Gone")
        daemon._agents["d"].registered_at = now - timedelta(minutes=1)

        actions = daemon.run_once()
        by_id = {a["agent_id"]: a for a in actions}

        assert by_id["a"]["action"] == "reset"
        assert by_id["b"]["action"] == "warn"
        assert by_id["c"]["action"] == "skip_gate"
        assert by_id["d"]["action"] == "exited"
        # Exited agent auto-unregistered
        assert "d" not in daemon.monitored_agents


# ---------------------------------------------------------------------------
# Stop control
# ---------------------------------------------------------------------------


class TestStopControl:
    def test_stop_sets_running_false(self, daemon) -> None:
        daemon._running = True
        daemon.stop()
        assert not daemon._running
