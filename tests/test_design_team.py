"""Tests for P1-03: Design Team composition (DESIGN.md §2.2, §3.4).

Covers:
- create_design_team: TeamMode.coordinate, db= required
- persist_design_output: writes to session_state, validates content, AC-06 error check
- revise_design_from_feedback: reads current design, structures revision prompt
- is_genuine_team_error: false-positive filtering for design content

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

from orchestra.utils.team import is_genuine_team_error
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
    """Test create_design_team via mocking the Agno SDK imports."""

    def test_creates_team_with_correct_config(self) -> None:
        """Verify create_design_team passes correct params to Team()."""
        mock_team_cls = MagicMock()
        mock_team_instance = MagicMock()
        mock_team_cls.return_value = mock_team_instance
        mock_teammode = MagicMock()
        mock_teammode.coordinate = "coordinate"

        with patch.dict(sys.modules, {
            "agno.models.anthropic": MagicMock(),
            "agno.team": MagicMock(Team=mock_team_cls),
            "agno.team.mode": MagicMock(TeamMode=mock_teammode),
        }), patch(
            "orchestra.model_resolver.instantiate_model"
        ) as mock_inst:
            mock_inst.return_value = MagicMock()
            import orchestra.workflow.design_team as dt_mod
            importlib.reload(dt_mod)

            db = MagicMock()
            dt_mod.create_design_team(db)

            mock_team_cls.assert_called_once()
            call_kwargs = mock_team_cls.call_args
            assert call_kwargs.kwargs["name"] == "Design Squad"
            assert call_kwargs.kwargs["mode"] == "coordinate"
            assert call_kwargs.kwargs["db"] is db
            assert callable(call_kwargs.kwargs["members"])
            # Verify instantiate_model was called for leader
            mock_inst.assert_called_once_with("claude-sonnet-4-6")

    def test_member_factory_is_callable(self) -> None:
        """Verify create_member_factory returns a callable."""
        from orchestra.agents.factory import create_member_factory
        factory = create_member_factory()
        assert callable(factory)


# ---------------------------------------------------------------------------
# is_genuine_team_error (false-positive filtering)
# ---------------------------------------------------------------------------


class TestIsGenuineTeamError:
    def test_traceback_is_genuine(self) -> None:
        errors = [
            "some context Traceback (most recent call last): more context"
        ]
        assert is_genuine_team_error(errors) is True

    def test_member_failed_is_genuine(self) -> None:
        errors = ["member Specialist-1 failed during execution"]
        assert is_genuine_team_error(errors) is True

    def test_error_occurred_during_execution_is_genuine(self) -> None:
        errors = ["Error occurred during execution of agent"]
        assert is_genuine_team_error(errors) is True

    def test_bare_error_word_is_not_genuine(self) -> None:
        """Design docs discussing error handling should NOT trigger."""
        errors = ["Error handling should use Result types"]
        assert is_genuine_team_error(errors) is False

    def test_bare_exception_word_is_not_genuine(self) -> None:
        errors = ["Exception propagation strategy for API calls"]
        assert is_genuine_team_error(errors) is False

    def test_empty_errors_not_genuine(self) -> None:
        assert is_genuine_team_error([]) is False

    def test_mixed_genuine_and_false_positive(self) -> None:
        """If any error is genuine, the whole set is genuine."""
        errors = [
            "Error handling should use Result types",
            "Traceback (most recent call last): File test.py",
        ]
        assert is_genuine_team_error(errors) is True


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

    def test_detects_genuine_team_member_errors_ac06(self) -> None:
        """AC-06: genuine Agno error injection must be detected."""
        error_content = (
            "Design output from team. Traceback (most recent call last): "
            "File 'agent.py', line 42, in run_member. " + "x" * 100
        )
        si = _make_step_input(previous_step_content=error_content)
        from orchestra.utils.team import TeamMemberError
        with pytest.raises(TeamMemberError):
            persist_design_output(si)

    def test_allows_design_discussing_errors(self) -> None:
        """Design content mentioning 'Error' legitimately should NOT trigger."""
        content = (
            "# Design Document\n\n"
            "## Error Handling Strategy\n"
            "All API calls should use Result types for error propagation.\n"
            "Exception handling follows the fail-fast pattern.\n"
            "Error codes are defined in the ErrorCode enum.\n"
            "## Components\n- Component A\n- Component B\n"
        )
        si = _make_step_input(previous_step_content=content)
        result = persist_design_output(si)
        assert result.content == content

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

    def test_revision_is_prompt_not_revised_design(self) -> None:
        """Verify the output is a revision prompt for the next team execution,
        not the revised design itself."""
        si = _make_step_input(
            previous_step_content="Add caching layer",
            session_state={"latest_design_content": "Original design"},
        )
        result = revise_design_from_feedback(si)
        assert "Please revise the design" in result.content
        assert "Review Feedback" in result.content

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
