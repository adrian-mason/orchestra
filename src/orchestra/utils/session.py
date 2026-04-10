"""Session state helpers (AC-03).

Encapsulates the actual Agno runtime path:
    step_input.workflow_session.session_data["session_state"][key]

All runtime code MUST use these helpers instead of accessing the path directly.
"""

from __future__ import annotations

from typing import Any

from agno.workflow.types import StepInput


def get_session_state(step_input: StepInput) -> dict[str, Any]:
    """Return the session_state dict from a StepInput, creating it if absent.

    Raises AssertionError if workflow_session or session_data is None,
    which indicates a misconfigured Workflow (missing db or session_state init).
    """
    assert step_input.workflow_session is not None, (
        "workflow_session is None — Workflow may not have been properly initialized"
    )
    sd = step_input.workflow_session.session_data
    assert sd is not None, (
        "session_data is None — Workflow may be missing db configuration (AC-05)"
    )
    if "session_state" not in sd:
        sd["session_state"] = {}
    ss = sd["session_state"]
    assert isinstance(ss, dict), (
        f"session_state must be a dict, got {type(ss).__name__} (AC-03). "
        "This indicates session_state was corrupted or initialized with a non-dict value."
    )
    return ss


def get_ss(step_input: StepInput, key: str, default: Any = None) -> Any:
    """Read a value from session_state. Returns default if key is missing."""
    return get_session_state(step_input).get(key, default)


def set_ss(step_input: StepInput, key: str, value: Any) -> None:
    """Write a value to session_state."""
    get_session_state(step_input)[key] = value
