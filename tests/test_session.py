"""Tests for orchestra.utils.session (AC-03)."""

import pytest

from agno.workflow.types import StepInput
from agno.session.workflow import WorkflowSession

from orchestra.utils.session import get_session_state, get_ss, set_ss


def _make_step_input(session_state: dict | None = None) -> StepInput:
    """Create a StepInput with a WorkflowSession for testing."""
    ws = WorkflowSession(session_id="test")
    if session_state is not None:
        ws.session_data["session_state"] = session_state
    return StepInput(workflow_session=ws)


class TestGetSessionState:
    def test_returns_existing_session_state(self):
        si = _make_step_input({"key": "value"})
        ss = get_session_state(si)
        assert ss == {"key": "value"}

    def test_creates_session_state_if_missing(self):
        si = _make_step_input()  # no session_state key in session_data
        ss = get_session_state(si)
        assert ss == {}
        # Verify it's persisted
        assert si.workflow_session.session_data["session_state"] is ss

    def test_fails_if_workflow_session_is_none(self):
        si = StepInput(workflow_session=None)
        with pytest.raises(AssertionError, match="workflow_session is None"):
            get_session_state(si)

    def test_fails_if_session_data_is_none(self):
        ws = WorkflowSession(session_id="test")
        ws.session_data = None
        si = StepInput(workflow_session=ws)
        with pytest.raises(AssertionError, match="session_data is None"):
            get_session_state(si)

    def test_fails_if_session_state_is_not_dict(self):
        si = _make_step_input()
        si.workflow_session.session_data["session_state"] = []
        with pytest.raises(AssertionError, match="must be a dict"):
            get_session_state(si)


class TestGetSs:
    def test_reads_existing_key(self):
        si = _make_step_input({"foo": 42})
        assert get_ss(si, "foo") == 42

    def test_returns_default_for_missing_key(self):
        si = _make_step_input({})
        assert get_ss(si, "missing") is None
        assert get_ss(si, "missing", "fallback") == "fallback"


class TestSetSs:
    def test_writes_key(self):
        si = _make_step_input({})
        set_ss(si, "new_key", "new_value")
        assert get_ss(si, "new_key") == "new_value"

    def test_overwrites_existing_key(self):
        si = _make_step_input({"key": "old"})
        set_ss(si, "key", "new")
        assert get_ss(si, "key") == "new"

    def test_writes_to_initially_empty_session(self):
        si = _make_step_input()
        set_ss(si, "first_write", True)
        assert get_ss(si, "first_write") is True
