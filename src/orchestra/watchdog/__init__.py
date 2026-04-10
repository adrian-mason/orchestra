"""Watchdog health monitoring system (DESIGN.md S7).

Tier 0: 30-second process monitoring with progressive escalation
(warn -> nudge) and Decision Gate awareness.
"""

from orchestra.watchdog.activity import ActivityState, get_agent_activity_state
from orchestra.watchdog.daemon import WatchdogDaemon

__all__ = [
    "ActivityState",
    "WatchdogDaemon",
    "get_agent_activity_state",
]
