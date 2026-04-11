"""Tests for P1-08: Per-Unit 4-Phase Loop (DESIGN.md §2.5-§2.6).

Covers:
- run_four_phase_loop: 4 phases, retry logic, escalation
- execute_work_units: DAG ordering, session state, batch execution
- Phase functions: implement, validate, review, commit
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestra.models.work_unit import WorkUnit
from orchestra.workflow.four_phase_loop import (
    MAX_ATTEMPTS,
    PhaseResult,
    UnitResult,
    execute_work_units,
    run_four_phase_loop,
)
from orchestra.workflow.quality_gates import GateResult, QualityGateResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_input(
    session_state: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock StepInput with a working session_state."""
    si = MagicMock()
    sd: dict[str, Any] = {"session_state": session_state or {}}
    si.workflow_session.session_data = sd
    si.workflow_session.session_id = "test-session-123"
    si.previous_step_content = ""
    return si


def _make_work_unit(
    id: str = "wu-001",
    title: str = "Implement auth",
    dependencies: list[str] | None = None,
) -> WorkUnit:
    return WorkUnit(
        id=id,
        title=title,
        description=f"Implement {title}",
        dod=["Tests pass", "Lint clean"],
        file_scope=[f"src/{id}/*.py"],
        dependencies=dependencies or [],
        estimated_complexity="M",
    )


def _make_work_unit_dict(**kwargs: Any) -> dict[str, Any]:
    return _make_work_unit(**kwargs).model_dump()


def _mock_impl_success():
    """Patch the implement phase to succeed."""
    mock_result = MagicMock()
    mock_result.content = "Implementation completed successfully"
    mock_agent = MagicMock()
    mock_agent.run.return_value = mock_result
    return patch("agno.agent.Agent", return_value=mock_agent)


def _mock_validate_success():
    """Patch quality gates to pass."""
    return patch(
        "orchestra.workflow.four_phase_loop.run_quality_gates",
        return_value=QualityGateResult(
            passed=True,
            results=[GateResult("test", True, "ok", 0)],
            summary="All passed",
        ),
    )


def _mock_validate_failure():
    """Patch quality gates to fail."""
    return patch(
        "orchestra.workflow.four_phase_loop.run_quality_gates",
        return_value=QualityGateResult(
            passed=False,
            results=[GateResult("test", False, "FAILED", 1)],
            summary="1 gate failed",
        ),
    )


def _mock_review_success():
    """Patch adversarial reviewers to approve."""
    mock_reviewer = MagicMock()
    mock_reviewer.name = "Reviewer1"
    mock_result = MagicMock()
    mock_result.content = '{"reviewer": "Reviewer1", "verdict": "APPROVED", "blockers": [], "reasoning": "LGTM"}'
    mock_reviewer.run.return_value = mock_result
    return patch(
        "orchestra.workflow.four_phase_loop.create_fresh_adversarial_reviewers",
        return_value=[mock_reviewer],
    )


def _mock_review_rejection():
    """Patch adversarial reviewers to reject."""
    mock_reviewer = MagicMock()
    mock_reviewer.name = "Reviewer1"
    mock_result = MagicMock()
    mock_result.content = '{"reviewer": "Reviewer1", "verdict": "REJECTED", "blockers": ["Missing tests"], "reasoning": "Incomplete"}'
    mock_reviewer.run.return_value = mock_result
    return patch(
        "orchestra.workflow.four_phase_loop.create_fresh_adversarial_reviewers",
        return_value=[mock_reviewer],
    )


def _mock_model_resolution():
    """Patch model resolution and instantiation."""
    return patch(
        "orchestra.workflow.four_phase_loop.resolve_model",
        return_value="claude-sonnet-4-6",
    ), patch("orchestra.workflow.four_phase_loop.instantiate_model")


# ---------------------------------------------------------------------------
# PhaseResult / UnitResult
# ---------------------------------------------------------------------------


class TestPhaseResult:
    def test_create_success(self) -> None:
        r = PhaseResult(phase="IMPLEMENT", success=True, output="done")
        assert r.phase == "IMPLEMENT"
        assert r.success is True

    def test_create_failure(self) -> None:
        r = PhaseResult(phase="VALIDATE", success=False, output="lint failed")
        assert r.success is False

    def test_frozen(self) -> None:
        r = PhaseResult(phase="COMMIT", success=True)
        with pytest.raises(AttributeError):
            r.phase = "OTHER"  # type: ignore[misc]


class TestUnitResult:
    def test_default_status_escalated(self) -> None:
        r = UnitResult(unit_id="wu-001", status="escalated")
        assert r.status == "escalated"
        assert r.attempts == 0
        assert r.phases == []

    def test_completed(self) -> None:
        r = UnitResult(unit_id="wu-001", status="completed", attempts=1)
        assert r.status == "completed"


# ---------------------------------------------------------------------------
# run_four_phase_loop
# ---------------------------------------------------------------------------


class TestRunFourPhaseLoop:
    def test_single_unit_all_phases_pass(self) -> None:
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_success(), _mock_review_success():
            result = run_four_phase_loop(wu)

        assert result.status == "completed"
        assert result.attempts == 1
        assert result.unit_id == "wu-001"
        assert result.assigned_model == "claude-sonnet-4-6"

    def test_validate_failure_retries(self) -> None:
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        # Validate fails all 3 times → escalated
        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_failure(), _mock_review_success():
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"
        assert result.attempts == MAX_ATTEMPTS

    def test_review_rejection_retries(self) -> None:
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_success(), _mock_review_rejection():
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"
        assert result.attempts == MAX_ATTEMPTS

    def test_retry_then_success(self) -> None:
        """Validate fails on attempt 1, succeeds on attempt 2."""
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        # First call fails, second succeeds
        validate_results = [
            QualityGateResult(passed=False, results=[], summary="failed"),
            QualityGateResult(passed=True, results=[], summary="passed"),
            QualityGateResult(passed=True, results=[], summary="passed"),
        ]

        with resolve_patch, inst_patch, \
             _mock_impl_success(), \
             patch("orchestra.workflow.four_phase_loop.run_quality_gates",
                   side_effect=validate_results), \
             _mock_review_success():
            result = run_four_phase_loop(wu)

        assert result.status == "completed"
        assert result.attempts == 2

    def test_assigns_model_via_resolve_model(self) -> None:
        wu = _make_work_unit()

        with patch("orchestra.workflow.four_phase_loop.resolve_model",
                   return_value="codex-gpt-5.3") as mock_resolve, \
             patch("orchestra.workflow.four_phase_loop.instantiate_model"), \
             _mock_impl_success(), _mock_validate_success(), _mock_review_success():
            result = run_four_phase_loop(wu, project="my-project")

        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["project"] == "my-project"
        assert result.assigned_model == "codex-gpt-5.3"
        assert wu.assigned_model == "codex-gpt-5.3"

    def test_max_attempts_is_3(self) -> None:
        assert MAX_ATTEMPTS == 3

    def test_escalated_after_max_attempts(self) -> None:
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_failure():
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"
        assert result.attempts == 3

    def test_uses_create_fresh_adversarial_reviewers(self) -> None:
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_success(), \
             _mock_review_success() as mock_reviewers:
            run_four_phase_loop(wu)

        mock_reviewers.assert_called_once_with("claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# execute_work_units
# ---------------------------------------------------------------------------


class TestExecuteWorkUnits:
    def _mock_four_phase(self, statuses: dict[str, str] | None = None):
        """Patch run_four_phase_loop to return specified statuses."""
        default_statuses = statuses or {}

        def side_effect(wu, **kwargs):
            status = default_statuses.get(wu.id, "completed")
            return UnitResult(
                unit_id=wu.id,
                status=status,
                attempts=1 if status == "completed" else 3,
                assigned_model="claude-sonnet-4-6",
            )

        return patch(
            "orchestra.workflow.four_phase_loop.run_four_phase_loop",
            side_effect=side_effect,
        )

    def test_reads_work_units_from_session_state(self) -> None:
        """AC-03: Must read from session_state."""
        units = [_make_work_unit_dict(id="wu-001")]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase():
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        assert "execution_results" in ss

    def test_raises_without_work_units(self) -> None:
        si = _make_step_input(session_state={})
        with pytest.raises(ValueError, match="No work_units"):
            execute_work_units(si)

    def test_stores_results_in_session_state(self) -> None:
        units = [
            _make_work_unit_dict(id="wu-001"),
            _make_work_unit_dict(id="wu-002", dependencies=["wu-001"]),
        ]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase():
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        assert len(ss["execution_results"]) == 2
        assert ss["execution_results"][0]["unit_id"] == "wu-001"
        assert ss["execution_results"][1]["unit_id"] == "wu-002"

    def test_respects_dag_ordering(self) -> None:
        """Units with dependencies execute after their dependencies."""
        units = [
            _make_work_unit_dict(id="wu-002", dependencies=["wu-001"]),
            _make_work_unit_dict(id="wu-001"),
        ]
        si = _make_step_input(session_state={"work_units": units})

        execution_order: list[str] = []

        def track_order(wu, **kwargs):
            execution_order.append(wu.id)
            return UnitResult(unit_id=wu.id, status="completed", attempts=1)

        with patch("orchestra.workflow.four_phase_loop.run_four_phase_loop",
                   side_effect=track_order):
            execute_work_units(si)

        assert execution_order.index("wu-001") < execution_order.index("wu-002")

    def test_tracks_escalated_units(self) -> None:
        units = [
            _make_work_unit_dict(id="wu-001"),
            _make_work_unit_dict(id="wu-002", dependencies=["wu-001"]),
        ]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase(statuses={"wu-002": "escalated"}):
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        assert ss["escalated_units"] == ["wu-002"]

    def test_all_completed_no_escalation(self) -> None:
        units = [_make_work_unit_dict(id="wu-001")]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase():
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        assert ss["escalated_units"] == []

    def test_returns_json_content(self) -> None:
        units = [_make_work_unit_dict(id="wu-001")]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase():
            result = execute_work_units(si)

        parsed = json.loads(result.content)
        assert len(parsed) == 1
        assert parsed[0]["unit_id"] == "wu-001"
        assert parsed[0]["status"] == "completed"

    def test_passes_project_name(self) -> None:
        units = [_make_work_unit_dict(id="wu-001")]
        si = _make_step_input(session_state={
            "work_units": units,
            "project_name": "test-project",
        })

        with patch("orchestra.workflow.four_phase_loop.run_four_phase_loop",
                   return_value=UnitResult(unit_id="wu-001", status="completed", attempts=1)
                   ) as mock_loop:
            execute_work_units(si)

        assert mock_loop.call_args.kwargs.get("project") == "test-project"

    def test_parallel_batch_execution(self) -> None:
        """Independent units (no deps) should be in same batch."""
        units = [
            _make_work_unit_dict(id="wu-001"),
            _make_work_unit_dict(id="wu-002"),
            _make_work_unit_dict(id="wu-003", dependencies=["wu-001", "wu-002"]),
        ]
        si = _make_step_input(session_state={"work_units": units})

        execution_order: list[str] = []

        def track_order(wu, **kwargs):
            execution_order.append(wu.id)
            return UnitResult(unit_id=wu.id, status="completed", attempts=1)

        with patch("orchestra.workflow.four_phase_loop.run_four_phase_loop",
                   side_effect=track_order):
            execute_work_units(si)

        # wu-001 and wu-002 must both execute before wu-003
        assert execution_order.index("wu-001") < execution_order.index("wu-003")
        assert execution_order.index("wu-002") < execution_order.index("wu-003")

    def test_blocks_units_with_escalated_dependencies(self) -> None:
        """Units whose deps failed should be blocked, not executed."""
        units = [
            _make_work_unit_dict(id="wu-001"),
            _make_work_unit_dict(id="wu-002", dependencies=["wu-001"]),
        ]
        si = _make_step_input(session_state={"work_units": units})

        with self._mock_four_phase(statuses={"wu-001": "escalated"}):
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        results = {r["unit_id"]: r["status"] for r in ss["execution_results"]}
        assert results["wu-001"] == "escalated"
        assert results["wu-002"] == "blocked"
        assert set(ss["escalated_units"]) == {"wu-001", "wu-002"}

    def test_serialized_results_include_last_failed_phase(self) -> None:
        """Serialization should include the last failed phase."""
        units = [_make_work_unit_dict(id="wu-001")]
        si = _make_step_input(session_state={"work_units": units})

        failed_result = UnitResult(
            unit_id="wu-001",
            status="escalated",
            attempts=3,
            phases=[
                PhaseResult(phase="IMPLEMENT", success=True, output="ok"),
                PhaseResult(phase="VALIDATE", success=False, output="lint failed"),
            ],
        )

        with patch("orchestra.workflow.four_phase_loop.run_four_phase_loop",
                   return_value=failed_result):
            execute_work_units(si)

        ss = si.workflow_session.session_data["session_state"]
        last_failed = ss["execution_results"][0]["last_failed_phase"]
        assert last_failed is not None
        assert last_failed["phase"] == "VALIDATE"
        assert "lint failed" in last_failed["output"]


# ---------------------------------------------------------------------------
# IMPLEMENT phase failure (BLOCKER #2)
# ---------------------------------------------------------------------------


class TestImplementPhaseFailure:
    def test_empty_content_returns_failure(self) -> None:
        """IMPLEMENT must fail if agent returns empty content."""
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        mock_result = MagicMock()
        mock_result.content = None

        with resolve_patch, inst_patch,              patch("agno.agent.Agent") as mock_cls,              _mock_validate_success(), _mock_review_success():
            mock_cls.return_value.run.return_value = mock_result
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"

    def test_error_content_returns_failure(self) -> None:
        """IMPLEMENT must fail on genuine execution errors."""
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        mock_result = MagicMock()
        mock_result.content = "Traceback (most recent call last): File agent.py error"

        with resolve_patch, inst_patch,              patch("agno.agent.Agent") as mock_cls,              _mock_validate_success(), _mock_review_success():
            mock_cls.return_value.run.return_value = mock_result
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"


class TestReviewPhaseFailure:
    def test_reviewer_error_returns_failure(self) -> None:
        """REVIEW must fail if reviewer agent has execution error."""
        wu = _make_work_unit()
        resolve_patch, inst_patch = _mock_model_resolution()

        mock_reviewer = MagicMock()
        mock_reviewer.name = "Reviewer1"
        mock_result = MagicMock()
        mock_result.content = "Traceback (most recent call last): File agent.py error"
        mock_reviewer.run.return_value = mock_result

        review_patch = patch(
            "orchestra.workflow.four_phase_loop.create_fresh_adversarial_reviewers",
            return_value=[mock_reviewer],
        )

        with resolve_patch, inst_patch, \
             _mock_impl_success(), _mock_validate_success(), review_patch:
            result = run_four_phase_loop(wu)

        assert result.status == "escalated"
        review_phases = [p for p in result.phases if p.phase == "REVIEW"]
        assert len(review_phases) > 0
        assert not review_phases[0].success
        assert "error" in review_phases[0].details.get("error", "").lower()
