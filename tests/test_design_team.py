"""Tests for P1-03: Design Team composition (DESIGN.md §2.2, §3.4).

Covers:
- create_design_team: TeamMode.coordinate, db= required
- persist_design_output: writes to session_state, validates content, AC-06 error check
- revise_design_from_feedback: reads current design, structures revision prompt

Factory logic (resolve_design_members, create_member_factory, SpecialistMapping)
is tested in test_callable_factory.py (P1-02). This module tests only the
workflow-level functions that consume the factory.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestra.workflow.design_team import (
    persist_design_output,
    revise_design_from_feedback,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_input(
    previous_step_content: str | None = "",
    session_state: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock StepInput with a working session_state."""
    si = MagicMock()
    sd: dict[str, Any] = {"session_state": session_state or {}}
    si.workflow_session.session_data = sd
    si.previous_step_content = previous_step_content
    return si


# ---------------------------------------------------------------------------
# create_design_team
# ---------------------------------------------------------------------------


class TestCreateDesignTeam:
    """Test create_design_team via mocking the Agno SDK imports.

    Since anthropic SDK is not installed in test env, we mock the
    entire create_design_team function's internal imports by patching
    at the workflow module level.
    """

    def test_creates_team_with_correct_config(self) -> None:
        """Verify create_design_team passes correct params to Team()."""
        mock_team_cls = MagicMock()
        mock_team_instance = MagicMock()
        mock_team_cls.return_value = mock_team_instance
        mock_claude = MagicMock()
        mock_teammode = MagicMock()
        mock_teammode.coordinate = "coordinate"

        with patch.dict(sys.modules, {
            "agno.models.anthropic": MagicMock(Claude=mock_claude),
            "agno.team": MagicMock(Team=mock_team_cls),
            "agno.team.mode": MagicMock(TeamMode=mock_teammode),
        }):
            # Force reimport to pick up mocks
            import orchestra.workflow.design_team as dt_mod
            importlib.reload(dt_mod)

            db = MagicMock()
            result = dt_mod.create_design_team(db)

            # Verify Team was called with correct params
            mock_team_cls.assert_called_once()
            call_kwargs = mock_team_cls.call_args
            assert call_kwargs.kwargs["name"] == "Design Squad"
            assert call_kwargs.kwargs["mode"] == "coordinate"
            assert call_kwargs.kwargs["db"] is db
            assert callable(call_kwargs.kwargs["members"])

    def test_member_factory_is_callable(self) -> None:
        """Verify create_member_factory returns a callable."""
        from orchestra.agents.factory import create_member_factory
        factory = create_member_factory()
        assert callable(factory)


# ---------------------------------------------------------------------------
# persist_design_output
# ---------------------------------------------------------------------------


class TestPersistDesignOutput:
    def test_writes_content_to_session_state(self) -> None:
        design = "A" * 100
        si = _make_step_input(previous_step_content=design)
        result = persist_design_output(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["latest_design_content"] == design
        assert result.content == design

    def test_rejects_empty_content(self) -> None:
        si = _make_step_input(previous_step_content="")
        with pytest.raises(ValueError, match="empty or too-short"):
            persist_design_output(si)

    def test_rejects_none_content(self) -> None:
        si = _make_step_input(previous_step_content=None)
        with pytest.raises(ValueError, match="empty or too-short"):
            persist_design_output(si)

    def test_rejects_short_content(self) -> None:
        si = _make_step_input(previous_step_content="Too short")
        with pytest.raises(ValueError, match="empty or too-short"):
            persist_design_output(si)

    def test_rejects_whitespace_only(self) -> None:
        si = _make_step_input(previous_step_content="   \n\t   ")
        with pytest.raises(ValueError, match="empty or too-short"):
            persist_design_output(si)

    def test_detects_team_member_errors_ac06(self) -> None:
        """AC-06: check_team_member_errors must be called on team output."""
        error_content = (
            "Design complete but Error occurred during execution of "
            "member Specialist-1. Traceback follows. " + "x" * 100
        )
        si = _make_step_input(previous_step_content=error_content)
        from orchestra.utils.team import TeamMemberError
        with pytest.raises(TeamMemberError):
            persist_design_output(si)

    def test_accepts_valid_design(self) -> None:
        content = (
            "# Design Document\n\n"
            "## Overview\nThis design covers the implementation of feature X.\n"
            "## Components\n- Component A\n- Component B\n"
        )
        si = _make_step_input(previous_step_content=content)
        result = persist_design_output(si)
        assert result.content == content


# ---------------------------------------------------------------------------
# revise_design_from_feedback
# ---------------------------------------------------------------------------


class TestReviseDesignFromFeedback:
    def test_combines_design_and_feedback(self) -> None:
        si = _make_step_input(
            previous_step_content="Missing handling for edge case X",
            session_state={"latest_design_content": "Original design content here"},
        )
        result = revise_design_from_feedback(si)
        assert "Original design content here" in result.content
        assert "Missing handling for edge case X" in result.content
        assert "Design Revision Request" in result.content

    def test_updates_session_state(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback here",
            session_state={"latest_design_content": "Original design"},
        )
        revise_design_from_feedback(si)
        ss = si.workflow_session.session_data["session_state"]
        assert "Feedback here" in ss["latest_design_content"]

    def test_raises_without_existing_design(self) -> None:
        si = _make_step_input(
            previous_step_content="Some feedback",
            session_state={},
        )
        with pytest.raises(ValueError, match="No design found"):
            revise_design_from_feedback(si)

    def test_raises_with_empty_existing_design(self) -> None:
        si = _make_step_input(
            previous_step_content="Some feedback",
            session_state={"latest_design_content": ""},
        )
        with pytest.raises(ValueError, match="No design found"):
            revise_design_from_feedback(si)
