"""Tests for P1-07: Work Unit Decomposition (DESIGN.md §2.5).

Covers:
- parse_work_units: JSON extraction, validation, error handling
- decompose_work_units: session state reads/writes, validation calls,
  AC-03 compliance, AC-06 error detection
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestra.models.work_unit import WorkUnit
from orchestra.workflow.dag import CyclicDependencyError, FileOverlapError
from orchestra.workflow.decomposition import (
    decompose_work_units,
    parse_work_units,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_input(
    previous_step_content: str = "",
    session_state: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock StepInput with a working session_state."""
    si = MagicMock()
    sd: dict[str, Any] = {"session_state": session_state or {}}
    si.workflow_session.session_data = sd
    si.workflow_session.session_id = "test-session-123"
    si.previous_step_content = previous_step_content
    return si


def _make_work_unit_dict(
    id: str = "wu-001",
    title: str = "Implement auth",
    description: str = "Add authentication middleware",
    dod: list[str] | None = None,
    file_scope: list[str] | None = None,
    dependencies: list[str] | None = None,
    estimated_complexity: str = "M",
) -> dict[str, Any]:
    """Create a work unit dict for testing."""
    return {
        "id": id,
        "title": title,
        "description": description,
        "dod": dod or ["Unit tests pass", "Lint clean"],
        "file_scope": file_scope or [f"src/{id}/*.py"],
        "dependencies": dependencies or [],
        "estimated_complexity": estimated_complexity,
    }


def _make_valid_units_json(count: int = 3) -> str:
    """Create a JSON array of valid work units."""
    units = []
    for i in range(count):
        wu_id = f"wu-{i+1:03d}"
        deps = [f"wu-{i:03d}"] if i > 0 else []
        units.append(_make_work_unit_dict(
            id=wu_id,
            title=f"Task {i+1}",
            description=f"Implement task {i+1}",
            file_scope=[f"src/task{i+1}/*.py"],
            dependencies=deps,
        ))
    return json.dumps(units)


def _make_fenced_json(count: int = 3) -> str:
    """Create a fenced code block with valid work units."""
    return f"```json\n{_make_valid_units_json(count)}\n```"


# ---------------------------------------------------------------------------
# parse_work_units
# ---------------------------------------------------------------------------


class TestParseWorkUnits:
    def test_parse_fenced_json(self) -> None:
        content = _make_fenced_json(2)
        units = parse_work_units(content)
        assert len(units) == 2
        assert units[0].id == "wu-001"
        assert units[1].id == "wu-002"

    def test_parse_raw_json_array(self) -> None:
        content = f"Here are the work units: {_make_valid_units_json(2)}"
        units = parse_work_units(content)
        assert len(units) == 2

    def test_parse_preserves_all_fields(self) -> None:
        wu_dict = _make_work_unit_dict(
            id="wu-test",
            title="Test Unit",
            description="Test description",
            dod=["Check 1", "Check 2"],
            file_scope=["src/test/*.py", "tests/test_*.py"],
            dependencies=["wu-001"],
            estimated_complexity="L",
        )
        content = json.dumps([wu_dict])
        units = parse_work_units(content)
        assert units[0].id == "wu-test"
        assert units[0].title == "Test Unit"
        assert units[0].dod == ["Check 1", "Check 2"]
        assert units[0].file_scope == ["src/test/*.py", "tests/test_*.py"]
        assert units[0].dependencies == ["wu-001"]
        assert units[0].estimated_complexity == "L"

    def test_raises_on_empty_content(self) -> None:
        with pytest.raises(ValueError, match="Empty content"):
            parse_work_units("")

    def test_raises_on_no_json_array(self) -> None:
        with pytest.raises(ValueError, match="No JSON array"):
            parse_work_units("Just some text with no JSON")

    def test_raises_on_json_object_not_array(self) -> None:
        # Wrap in array brackets context so it finds the `[` from surrounding text
        content = "Results [see below]: " + json.dumps({"id": "wu-001"})
        with pytest.raises(ValueError):
            parse_work_units(content)

    def test_raises_on_empty_array(self) -> None:
        with pytest.raises(ValueError, match="Empty work unit list"):
            parse_work_units("[]")

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_work_units("[{broken json}]")

    def test_raises_on_missing_required_field(self) -> None:
        content = json.dumps([{"id": "wu-001", "title": "Test"}])
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_work_units(content)

    def test_raises_on_invalid_complexity(self) -> None:
        wu = _make_work_unit_dict(estimated_complexity="XL")
        content = json.dumps([wu])
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_work_units(content)

    def test_returns_work_unit_instances(self) -> None:
        content = _make_valid_units_json(1)
        units = parse_work_units(content)
        assert isinstance(units[0], WorkUnit)

    def test_parse_with_surrounding_text(self) -> None:
        content = (
            "I've analyzed the design. Here are the work units:\n\n"
            f"```json\n{_make_valid_units_json(2)}\n```\n\n"
            "Let me know if you need changes."
        )
        units = parse_work_units(content)
        assert len(units) == 2


# ---------------------------------------------------------------------------
# decompose_work_units
# ---------------------------------------------------------------------------


class TestDecomposeWorkUnits:
    def _patch_agent(self, return_content: str):
        """Create patches for Agent and instantiate_model."""
        mock_result = MagicMock()
        mock_result.content = return_content
        mock_agent = MagicMock()
        mock_agent.run.return_value = mock_result
        return patch("agno.agent.Agent", return_value=mock_agent), \
               patch("orchestra.workflow.decomposition.instantiate_model")

    def test_reads_approved_design_from_session_state(self) -> None:
        """AC-03: Must read from session_state, not previous_step_content."""
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            previous_step_content="This should be ignored",
            session_state={"approved_design": "The real design content"},
        )

        with agent_patch as mock_agent_cls, model_patch:
            decompose_work_units(si)
            # Verify agent was called with approved_design content
            call_args = mock_agent_cls.return_value.run.call_args[0][0]
            assert "The real design content" in call_args
            assert "This should be ignored" not in call_args

    def test_raises_without_approved_design(self) -> None:
        si = _make_step_input(session_state={})
        with pytest.raises(ValueError, match="No approved_design"):
            decompose_work_units(si)

    def test_raises_with_empty_approved_design(self) -> None:
        si = _make_step_input(session_state={"approved_design": "   "})
        with pytest.raises(ValueError, match="No approved_design"):
            decompose_work_units(si)

    def test_stores_work_units_in_session_state(self) -> None:
        valid_json = _make_valid_units_json(3)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch:
            decompose_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        assert "work_units" in ss
        assert len(ss["work_units"]) == 3
        assert ss["work_unit_count"] == 3

    def test_stores_serialized_work_units(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch:
            decompose_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        # Verify work_units are dicts (serialized), not WorkUnit objects
        assert isinstance(ss["work_units"][0], dict)
        assert ss["work_units"][0]["id"] == "wu-001"

    def test_returns_json_content(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch:
            result = decompose_work_units(si)

        parsed = json.loads(result.content)
        assert len(parsed) == 2

    def test_includes_github_issues_when_present(self) -> None:
        valid_json = _make_valid_units_json(1)
        agent_patch, model_patch = self._patch_agent(valid_json)

        issues = [{"number": 1, "title": "Fix bug"}]
        si = _make_step_input(
            session_state={
                "approved_design": "Design content",
                "github_issues": issues,
            },
        )

        with agent_patch as mock_agent_cls, model_patch:
            decompose_work_units(si)
            call_args = mock_agent_cls.return_value.run.call_args[0][0]
            assert "GitHub Issues" in call_args
            assert "Fix bug" in call_args

    def test_omits_github_issues_section_when_empty(self) -> None:
        valid_json = _make_valid_units_json(1)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch as mock_agent_cls, model_patch:
            decompose_work_units(si)
            call_args = mock_agent_cls.return_value.run.call_args[0][0]
            assert "GitHub Issues" not in call_args

    def test_calls_validate_no_overlap(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch, \
             patch("orchestra.workflow.decomposition.validate_no_overlap") as mock_overlap:
            decompose_work_units(si)
            mock_overlap.assert_called_once()
            units = mock_overlap.call_args[0][0]
            assert len(units) == 2
            assert all(isinstance(u, WorkUnit) for u in units)

    def test_calls_validate_dag(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch, \
             patch("orchestra.workflow.decomposition.validate_dag") as mock_dag:
            decompose_work_units(si)
            mock_dag.assert_called_once()

    def test_propagates_overlap_error(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch, \
             patch("orchestra.workflow.decomposition.validate_no_overlap",
                   side_effect=FileOverlapError("Overlap detected")):
            with pytest.raises(FileOverlapError, match="Overlap detected"):
                decompose_work_units(si)

    def test_propagates_cycle_error(self) -> None:
        valid_json = _make_valid_units_json(2)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch, \
             patch("orchestra.workflow.decomposition.validate_dag",
                   side_effect=CyclicDependencyError("Cycle found")):
            with pytest.raises(CyclicDependencyError, match="Cycle found"):
                decompose_work_units(si)

    def test_detects_genuine_team_errors_ac06(self) -> None:
        """AC-06: Must detect genuine agent execution errors."""
        error_content = (
            "member Decomposer failed during execution. "
            "Traceback follows: some stack trace. " + "x" * 50
        )
        agent_patch, model_patch = self._patch_agent(error_content)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        from orchestra.utils.team import TeamMemberError

        with agent_patch, model_patch:
            with pytest.raises(TeamMemberError):
                decompose_work_units(si)

    def test_uses_instantiate_model(self) -> None:
        """Model must be created via instantiate_model, not direct import."""
        valid_json = _make_valid_units_json(1)
        agent_patch, model_patch = self._patch_agent(valid_json)

        si = _make_step_input(
            session_state={"approved_design": "Design content"},
        )

        with agent_patch, model_patch as mock_model:
            decompose_work_units(si)
            mock_model.assert_called_once_with("claude-sonnet-4-6")
