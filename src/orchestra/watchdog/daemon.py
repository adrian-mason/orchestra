"""Watchdog Daemon Tier 0: 30-second process monitoring.

DESIGN.md S7.2: Implements Tier 0 progressive escalation:
- Level 1: Log warning (agent idle)
- Level 2: Send nudge message
- Decision Gate aware: skips agents in WAITING_INPUT state
- Calls reap_expired_gates() at end of each monitor cycle

Tier 1 (AI triage) and Tier 2 (persistent monitoring) are deferred to P2-10.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from orchestra.observability.logger import SessionLogger
from orchestra.watchdog.activity import ActivityState, get_agent_activity_state
from orchestra.workflow.gate import reap_expired_gates

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb

logger = logging.getLogger(__name__)


class AgentHandle(Protocol):
    """Minimal interface for an agent being monitored."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def current_run_id(self) -> str | None: ...


@dataclass
class MonitoredAgent:
    """Internal tracking state for a monitored agent."""

    agent_id: str
    agent_name: str
    current_run_id: str | None = None
    escalation_level: int = 0


@dataclass
class WatchdogDaemon:
    """Tier 0 watchdog: 30-second health monitoring with progressive escalation.

    Monitors registered agents via ActivityState time-decay model.
    Escalation policy (Tier 0 only):
        - IDLE level 1: log warning
        - IDLE level 2: send nudge
        - IDLE level 3+: log critical (AI triage deferred to P2-10)
        - WAITING_INPUT: skip (Decision Gate aware)
        - BLOCKED: log warning, no escalation
        - EXITED: log error, invoke on_agent_exited callback
        - ACTIVE/READY: reset escalation counter

    Usage::

        daemon = WatchdogDaemon(events_db=db, check_interval_sec=30)
        daemon.register_agent("reviewer", "reviewer-agent")
        daemon.run_once()  # single cycle (sync)
        await daemon.run()  # continuous loop (async, uses asyncio.sleep)
    """

    events_db: SqliteDb
    check_interval_sec: int = 30
    _agents: dict[str, MonitoredAgent] = field(default_factory=dict)
    _running: bool = False
    _session_logger: SessionLogger | None = None

    # Callbacks for extensibility
    _on_nudge: Any = None
    _on_agent_exited: Any = None

    def register_agent(
        self,
        agent_id: str,
        agent_name: str,
        *,
        current_run_id: str | None = None,
    ) -> None:
        """Register an agent for monitoring."""
        self._agents[agent_id] = MonitoredAgent(
            agent_id=agent_id,
            agent_name=agent_name,
            current_run_id=current_run_id,
        )

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from monitoring."""
        self._agents.pop(agent_id, None)

    @property
    def monitored_agents(self) -> dict[str, MonitoredAgent]:
        """Return a copy of monitored agents."""
        return dict(self._agents)

    def set_session_logger(self, session_logger: SessionLogger) -> None:
        """Attach a session logger for structured logging."""
        self._session_logger = session_logger

    def set_on_nudge(self, callback: Any) -> None:
        """Set callback for nudge events: callback(agent_id, agent_name)."""
        self._on_nudge = callback

    def set_on_agent_exited(self, callback: Any) -> None:
        """Set callback for agent exit events: callback(agent_id, agent_name)."""
        self._on_agent_exited = callback

    # -- Core monitoring --

    async def run(self) -> None:
        """Start the monitor loop. Runs until stop() is called."""
        self._running = True
        while self._running:
            self.run_once()
            await asyncio.sleep(self.check_interval_sec)

    def stop(self) -> None:
        """Signal the monitor loop to stop after current cycle."""
        self._running = False

    def run_once(self) -> list[dict[str, Any]]:
        """Execute a single monitoring cycle.

        Returns a list of action records for testing/observability:
        [{"agent_id": ..., "state": ..., "action": ..., "level": ...}, ...]
        """
        actions: list[dict[str, Any]] = []

        for agent_id, agent in list(self._agents.items()):
            state = get_agent_activity_state(
                self.events_db,
                agent.agent_id,
                workflow_run_id=agent.current_run_id,
            )

            action = self._handle_state(agent, state)
            actions.append(action)

        # End of cycle: reap expired Decision Gates (S4.6 TTL Reaper)
        reaped = reap_expired_gates(self.events_db)
        if reaped:
            self._log(
                "INFO",
                "gates_reaped",
                count=len(reaped),
                gate_ids=[g.id for g in reaped],
            )

        return actions

    def _handle_state(
        self,
        agent: MonitoredAgent,
        state: ActivityState,
    ) -> dict[str, Any]:
        """Process a single agent's state and apply escalation policy."""
        action_record: dict[str, Any] = {
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_name,
            "state": state.value,
            "action": "none",
            "escalation_level": agent.escalation_level,
        }

        match state:
            case ActivityState.ACTIVE | ActivityState.READY:
                # Healthy — reset escalation
                agent.escalation_level = 0
                action_record["action"] = "reset"

            case ActivityState.WAITING_INPUT:
                # Decision Gate pending — skip, no escalation
                action_record["action"] = "skip_gate"

            case ActivityState.BLOCKED:
                # Dependency blocked — log but don't escalate
                self._log(
                    "WARNING",
                    "agent_blocked",
                    agent_id=agent.agent_id,
                    agent_name=agent.agent_name,
                    reason="blocked on dependency",
                )
                action_record["action"] = "log_blocked"

            case ActivityState.IDLE:
                # Progressive escalation
                agent.escalation_level += 1
                action_record["escalation_level"] = agent.escalation_level

                match agent.escalation_level:
                    case 1:
                        # Tier 0: warn
                        self._log(
                            "WARNING",
                            "agent_idle",
                            agent_id=agent.agent_id,
                            agent_name=agent.agent_name,
                            escalation_level=1,
                            reason="idle",
                        )
                        action_record["action"] = "warn"
                    case 2:
                        # Tier 0: nudge
                        self._log(
                            "WARNING",
                            "agent_nudge",
                            agent_id=agent.agent_id,
                            agent_name=agent.agent_name,
                            escalation_level=2,
                            reason="idle — sending nudge",
                        )
                        self._send_nudge(agent)
                        action_record["action"] = "nudge"
                    case _:
                        # Tier 1+ deferred to P2-10
                        self._log(
                            "CRITICAL",
                            "agent_stuck",
                            agent_id=agent.agent_id,
                            agent_name=agent.agent_name,
                            escalation_level=agent.escalation_level,
                            reason="idle — AI triage deferred to P2-10",
                        )
                        action_record["action"] = "stuck_deferred"

            case ActivityState.EXITED:
                # Process exited — log error
                self._log(
                    "ERROR",
                    "agent_exited",
                    agent_id=agent.agent_id,
                    agent_name=agent.agent_name,
                )
                if self._on_agent_exited:
                    self._on_agent_exited(agent.agent_id, agent.agent_name)
                action_record["action"] = "exited"

        return action_record

    def _send_nudge(self, agent: MonitoredAgent) -> None:
        """Send a nudge to an idle agent."""
        if self._on_nudge:
            self._on_nudge(agent.agent_id, agent.agent_name)

    def _log(self, level: str, event: str, **kv: Any) -> None:
        """Log via session logger if available, otherwise stdlib logger."""
        if self._session_logger:
            self._session_logger.log(level, event, **kv)
        else:
            log_fn = getattr(logger, level.lower(), logger.info)
            log_fn(f"{event} {kv}")
