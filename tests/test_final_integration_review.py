"""Tests for final_integration_review (P1-09).

Covers: all-pass, quality gate failure, review rejection, no completed units,
reviewer execution error, decision gate creation on failure.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestra.utils.team import has_genuine_error
from orchestra.workflow.final_integration_review import (
    _build_integration_prompt,
    _parse_integration_verdicts,
    create_check_integration_result,
    final_integration_review,
)
from orchestra.workflow.quality_gates import GateResult, QualityGateResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step_input(session_state: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock StepInput with session_state."""
    si = MagicMock()
    ss = session_state or {}
    si.workflow_session.session_data = {"session_state": ss}
    si.workflow_session.session_id = "test-run-001"
    return si


def _make_execution_results(
    completed: int = 2,
    escalated: int = 0,
) -> list[dict[str, Any]]:
    """Create mock execution results."""
    results = []
    for i in range(completed):
        results.append({
            "unit_id": f"wu-{i+1}",
            "status": "completed",
            "attempts": 1,
            "assigned_model": "claude-sonnet-4-6",
        })
    for i in range(escalated):
        results.append({
            "unit_id": f"wu-esc-{i+1}",
            "status": "escalated",
            "attempts": 3,
            "assigned_model": "claude-sonnet-4-6",
        })
    return results


def _make_work_units(count: int = 2) -> list[dict[str, Any]]:
    """Create mock work unit dicts."""
    return [
        {
            "id": f"wu-{i+1}",
            "title": f"Work Unit {i+1}",
            "description": f"Description for WU {i+1}",
            "dod": [f"DoD item {i+1}"],
            "file_scope": [f"src/module_{i+1}.py"],
            "dependencies": [] if i == 0 else [f"wu-{i}"],
            "estimated_complexity": "M",
        }
        for i in range(count)
    ]


def _mock_quality_gates_pass():
    return patch(
        "orchestra.workflow.final_integration_review.run_quality_gates",
        return_value=QualityGateResult(
            passed=True,
            results=[GateResult("test", True, "ok", 0)],
            summary="All gates passed",
        ),
    )


def _mock_quality_gates_fail():
    return patch(
        "orchestra.workflow.final_integration_review.run_quality_gates",
        return_value=QualityGateResult(
            passed=False,
            results=[GateResult("test", False, "FAILED", 1)],
            summary="1 gate failed",
        ),
    )


def _mock_review_approved():
    mock_reviewer = MagicMock()
    mock_reviewer.name = "IntegrationReviewer"
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "reviewer": "IntegrationReviewer",
        "verdict": "APPROVED",
        "reasoning": "All units integrate cleanly",
        "blockers": [],
        "suggestions": [],
    })
    mock_reviewer.run.return_value = mock_result
    return patch(
        "orchestra.workflow.final_integration_review.create_fresh_adversarial_reviewers",
        return_value=[mock_reviewer],
    )


def _mock_review_rejected():
    mock_reviewer = MagicMock()
    mock_reviewer.name = "IntegrationReviewer"
    mock_result = MagicMock()
    mock_result.content = json.dumps({
        "reviewer": "IntegrationReviewer",
        "verdict": "REJECTED",
        "reasoning": "Interface mismatch between WU-1 and WU-2",
        "blockers": ["Type mismatch in shared module"],
        "suggestions": [],
    })
    mock_reviewer.run.return_value = mock_result
    return patch(
        "orchestra.workflow.final_integration_review.create_fresh_adversarial_reviewers",
        return_value=[mock_reviewer],
    )


def _mock_review_error():
    mock_reviewer = MagicMock()
    mock_reviewer.name = "IntegrationReviewer"
    mock_result = MagicMock()
    mock_result.content = "Traceback (most recent call last): File agent.py error"
    mock_reviewer.run.return_value = mock_result
    return patch(
        "orchestra.workflow.final_integration_review.create_fresh_adversarial_reviewers",
        return_value=[mock_reviewer],
    )


# ---------------------------------------------------------------------------
# _build_integration_prompt
# ---------------------------------------------------------------------------

class TestBuildIntegrationPrompt:
    def test_includes_completed_units(self) -> None:
        results = _make_execution_results(completed=2)
        units = _make_work_units(2)
        prompt = _build_integration_prompt(results, units)

        assert "wu-1" in prompt
        assert "wu-2" in prompt
        assert "Work Unit 1" in prompt
        assert "Work Unit 2" in prompt

    def test_includes_escalated_warning(self) -> None:
        results = _make_execution_results(completed=1, escalated=1)
        units = _make_work_units(1)
        prompt = _build_integration_prompt(results, units)

        assert "escalated" in prompt.lower()
        assert "wu-esc-1" in prompt

    def test_includes_review_instructions(self) -> None:
        results = _make_execution_results(completed=1)
        units = _make_work_units(1)
        prompt = _build_integration_prompt(results, units)

        assert "Interface consistency" in prompt
        assert "verdict" in prompt.lower()

    def test_includes_file_scope(self) -> None:
        results = _make_execution_results(completed=1)
        units = _make_work_units(1)
        prompt = _build_integration_prompt(results, units)

        assert "src/module_1.py" in prompt

    def test_includes_dependencies(self) -> None:
        results = _make_execution_results(completed=2)
        units = _make_work_units(2)
        prompt = _build_integration_prompt(results, units)

        assert "wu-1" in prompt


# ---------------------------------------------------------------------------
# _parse_integration_verdicts
# ---------------------------------------------------------------------------

class TestParseIntegrationVerdicts:
    def test_parses_approved_verdict(self) -> None:
        content = '{"reviewer": "R1", "verdict": "APPROVED", "reasoning": "ok"}'
        verdicts = _parse_integration_verdicts(content)
        assert len(verdicts) == 1
        assert verdicts[0]["verdict"] == "APPROVED"

    def test_parses_rejected_verdict(self) -> None:
        content = '{"reviewer": "R1", "verdict": "REJECTED", "blockers": ["issue"]}'
        verdicts = _parse_integration_verdicts(content)
        assert len(verdicts) == 1
        assert verdicts[0]["verdict"] == "REJECTED"

    def test_parses_multiple_verdicts(self) -> None:
        content = (
            '{"reviewer": "R1", "verdict": "APPROVED"}\n\n'
            '{"reviewer": "R2", "verdict": "REJECTED"}'
        )
        verdicts = _parse_integration_verdicts(content)
        assert len(verdicts) == 2

    def test_returns_empty_on_no_verdicts(self) -> None:
        verdicts = _parse_integration_verdicts("no json here")
        assert verdicts == []

    def test_ignores_invalid_json(self) -> None:
        content = '{"verdict": INVALID}'
        verdicts = _parse_integration_verdicts(content)
        assert verdicts == []


# ---------------------------------------------------------------------------
# has_genuine_error
# ---------------------------------------------------------------------------

class TestIsGenuineError:
    def test_traceback_is_genuine(self) -> None:
        assert has_genuine_error("Traceback (most recent call last): something")

    def test_normal_content_not_error(self) -> None:
        assert not has_genuine_error("The implementation looks correct")

    def test_empty_not_error(self) -> None:
        assert not has_genuine_error("")


# ---------------------------------------------------------------------------
# final_integration_review — all pass
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewPass:
    def test_all_pass(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_approved():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is True
        assert data["gates_passed"] is True
        assert data["review_passed"] is True

        ss = si.workflow_session.session_data["session_state"]
        assert ss["integration_gate_passed"] is True

    def test_writes_summary_to_session(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_approved():
            final_integration_review(si)

        ss = si.workflow_session.session_data["session_state"]
        assert "integration_review_summary" in ss
        assert "PASSED" in ss["integration_review_summary"]


# ---------------------------------------------------------------------------
# final_integration_review — quality gate failure
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewGateFailure:
    def test_gates_fail_means_integration_fails(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_fail(), _mock_review_approved():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is False
        assert data["gates_passed"] is False
        assert data["review_passed"] is True

    def test_both_fail(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_fail(), _mock_review_rejected():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is False
        assert data["gates_passed"] is False
        assert data["review_passed"] is False


# ---------------------------------------------------------------------------
# final_integration_review — review rejection
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewRejection:
    def test_review_rejection_fails_integration(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_rejected():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is False
        assert data["gates_passed"] is True
        assert data["review_passed"] is False

    def test_stores_rejection_verdicts(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_rejected():
            final_integration_review(si)

        ss = si.workflow_session.session_data["session_state"]
        verdicts = ss["integration_gate_verdicts"]
        assert len(verdicts) == 1
        assert verdicts[0]["verdict"] == "REJECTED"


# ---------------------------------------------------------------------------
# final_integration_review — no completed units
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewNoCompleted:
    def test_no_completed_units_fails(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=0, escalated=3),
            "work_units": _make_work_units(3),
        })

        result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is False
        assert "No completed" in data["summary"]

    def test_no_execution_results_raises(self) -> None:
        si = _make_step_input({})

        with pytest.raises(ValueError, match="No execution_results"):
            final_integration_review(si)


# ---------------------------------------------------------------------------
# final_integration_review — reviewer execution error
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewError:
    def test_reviewer_error_fails_integration(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_error():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert data["integration_passed"] is False
        assert "error" in data["summary"].lower()


# ---------------------------------------------------------------------------
# final_integration_review — escalated units in summary
# ---------------------------------------------------------------------------

class TestFinalIntegrationReviewEscalated:
    def test_escalated_units_in_summary(self) -> None:
        si = _make_step_input({
            "execution_results": _make_execution_results(completed=2, escalated=1),
            "work_units": _make_work_units(2),
        })

        with _mock_quality_gates_pass(), _mock_review_approved():
            result = final_integration_review(si)

        data = json.loads(result.content)
        assert "escalated" in data["summary"]


# ---------------------------------------------------------------------------
# create_check_integration_result — closure factory
# ---------------------------------------------------------------------------

class TestCheckIntegrationResult:
    def test_factory_returns_callable(self) -> None:
        mock_db = MagicMock()
        fn = create_check_integration_result(mock_db)
        assert callable(fn)

    def test_passed_returns_success(self) -> None:
        mock_db = MagicMock()
        fn = create_check_integration_result(mock_db)
        si = _make_step_input({"integration_gate_passed": True})

        result = fn(si)
        assert result.content == "INTEGRATION_REVIEW_PASSED"

    def test_failed_creates_decision_gate(self) -> None:
        mock_db = MagicMock()
        fn = create_check_integration_result(mock_db)
        si = _make_step_input({
            "integration_gate_passed": False,
            "integration_gate_verdicts": [
                {
                    "reviewer": "R1",
                    "verdict": "REJECTED",
                    "reasoning": "Interface mismatch",
                    "blockers": ["Type error"],
                    "suggestions": [],
                }
            ],
            "integration_review_summary": "integration review: REJECTED",
        })

        with patch(
            "orchestra.workflow.final_integration_review.create_decision_gate"
        ) as mock_gate:
            mock_gate.return_value = MagicMock(id="dg-test123")
            result = fn(si)

        assert result.content == "INTEGRATION_REVIEW_FAILED"
        mock_gate.assert_called_once()
        call_kwargs = mock_gate.call_args
        assert call_kwargs.kwargs["gate_type"] == "integration"
        assert call_kwargs.kwargs["agent_id"] == "integration_review_gate"

        ss = si.workflow_session.session_data["session_state"]
        assert ss["pending_decision_gate_id"] == "dg-test123"

    def test_failed_no_verdicts_still_creates_gate(self) -> None:
        mock_db = MagicMock()
        fn = create_check_integration_result(mock_db)
        si = _make_step_input({
            "integration_gate_passed": False,
            "integration_gate_verdicts": [],
        })

        with patch(
            "orchestra.workflow.final_integration_review.create_decision_gate"
        ) as mock_gate:
            mock_gate.return_value = MagicMock(id="dg-empty")
            result = fn(si)

        assert result.content == "INTEGRATION_REVIEW_FAILED"
        mock_gate.assert_called_once()
