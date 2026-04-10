"""Tests for P1-03: Design Team composition (DESIGN.md §2.2, §3.4).

Covers:
- resolve_design_members: architect always present, specialists loaded by tag
- create_member_factory: callable returns Agent list
- create_design_team: TeamMode.coordinate, db= required
- persist_design_output: writes to session_state, validates content, AC-06 error check
- revise_design_from_feedback: reads current design, structures revision prompt
- TAG_TO_SPECIALIST mapping completeness
"""

from __future__ import annotations

from typing import Any
import sys
from unittest.mock import MagicMock, patch

import pytest

from orchestra.agents.definitions import AgentRole, get_role_config
from orchestra.workflow.design_team import (
    TAG_TO_SPECIALIST,
    persist_design_output,
    resolve_design_members,
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
# resolve_design_members
# ---------------------------------------------------------------------------


class TestResolveDesignMembers:
    def test_architect_always_present(self) -> None:
        members = resolve_design_members({})
        assert len(members) == 1
        assert members[0]["name"] == "Architect"

    def test_architect_uses_correct_role(self) -> None:
        members = resolve_design_members({})
        architect_cfg = get_role_config(AgentRole.ARCHITECT)
        assert members[0]["model"] == architect_cfg.default_model

    def test_no_tags_no_specialists(self) -> None:
        members = resolve_design_members({"project_tags": []})
        assert len(members) == 1

    def test_unknown_tags_ignored(self) -> None:
        members = resolve_design_members({"project_tags": ["unknown", "nope"]})
        assert len(members) == 1

    def test_ebpf_tag_adds_probe(self) -> None:
        members = resolve_design_members({"project_tags": ["ebpf"]})
        assert len(members) == 2
        assert members[1]["name"] == "Probe"
        assert members[1]["role"] == "eBPF Domain Expert"

    def test_frontend_tag_adds_artisan(self) -> None:
        members = resolve_design_members({"project_tags": ["frontend"]})
        assert len(members) == 2
        assert members[1]["name"] == "Artisan"

    def test_data_tag_adds_oracle(self) -> None:
        members = resolve_design_members({"project_tags": ["data"]})
        assert len(members) == 2
        assert members[1]["name"] == "Oracle"

    def test_security_tag_adds_sentinel(self) -> None:
        members = resolve_design_members({"project_tags": ["security"]})
        assert len(members) == 2
        assert members[1]["name"] == "Sentinel"

    def test_multiple_tags_add_multiple_specialists(self) -> None:
        members = resolve_design_members(
            {"project_tags": ["ebpf", "security", "data"]}
        )
        assert len(members) == 4  # Architect + 3 specialists
        names = [m["name"] for m in members]
        assert "Architect" in names
        assert "Probe" in names
        assert "Sentinel" in names
        assert "Oracle" in names

    def test_all_tags_add_all_specialists(self) -> None:
        members = resolve_design_members(
            {"project_tags": list(TAG_TO_SPECIALIST.keys())}
        )
        assert len(members) == 1 + len(TAG_TO_SPECIALIST)

    def test_specialists_use_specialist_role_model(self) -> None:
        specialist_cfg = get_role_config(AgentRole.SPECIALIST)
        members = resolve_design_members({"project_tags": ["ebpf"]})
        assert members[1]["model"] == specialist_cfg.default_model

    def test_specialists_have_extra_instructions(self) -> None:
        members = resolve_design_members({"project_tags": ["security"]})
        assert "Sentinel" in members[1]["instructions"]
        assert "security" in members[1]["instructions"].lower()

    def test_each_member_has_required_keys(self) -> None:
        members = resolve_design_members(
            {"project_tags": ["ebpf", "frontend"]}
        )
        required_keys = {"name", "model", "instructions", "description", "role"}
        for member in members:
            assert required_keys.issubset(member.keys()), (
                f"Member {member.get('name')} missing keys: "
                f"{required_keys - member.keys()}"
            )

    def test_config_and_project_forwarded(self) -> None:
        """Verify config/project params are forwarded to create_agent."""
        with patch("orchestra.workflow.design_team.create_agent") as mock_create:
            mock_create.return_value = {
                "name": "Test",
                "model": "test-model",
                "instructions": "test",
                "description": "test",
                "role": "test",
                "team_mode": None,
            }
            config = MagicMock()
            resolve_design_members(
                {"project_tags": ["ebpf"]},
                config=config,
                project="test-project",
            )
            assert mock_create.call_count == 2
            for call in mock_create.call_args_list:
                assert call.kwargs.get("config") is config
                assert call.kwargs.get("project") == "test-project"


# ---------------------------------------------------------------------------
# TAG_TO_SPECIALIST completeness
# ---------------------------------------------------------------------------


class TestTagMapping:
    def test_four_specialist_tags_defined(self) -> None:
        assert len(TAG_TO_SPECIALIST) == 4

    def test_each_tag_has_name_and_description(self) -> None:
        for tag, (name, desc) in TAG_TO_SPECIALIST.items():
            assert isinstance(tag, str) and tag
            assert isinstance(name, str) and name
            assert isinstance(desc, str) and desc

    def test_specialist_names_are_unique(self) -> None:
        names = [name for name, _ in TAG_TO_SPECIALIST.values()]
        assert len(names) == len(set(names))


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
            import importlib
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
        from orchestra.workflow.design_team import create_member_factory
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
