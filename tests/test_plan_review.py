"""Tests for P1-04: Plan Review Gate (DESIGN.md §4.3).

Covers:
- GateVerdict model: creation, serialization, validation
- parse_verdicts: JSON extraction from various formats
- format_feedback: structured markdown output
- check_plan_gate: session state writes, pass/fail logic, AC-06 error check
- plan_review_approved: end_condition for Loop (AC-01)
- check_plan_review_result: post-Loop decision gate creation (AC-04)
- revise_plan_from_feedback: plan revision with feedback integration
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agno.workflow.types import StepOutput

from orchestra.workflow.plan_review import (
    GateVerdict,
    check_plan_gate,
    check_plan_review_result,
    format_feedback,
    parse_verdicts,
    plan_review_approved,
    revise_plan_from_feedback,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_input(
    previous_step_content: str = "",
    session_state: dict[str, Any] | None = None,
    db: Any = None,
) -> MagicMock:
    """Build a mock StepInput with a working session_state."""
    si = MagicMock()
    sd: dict[str, Any] = {"session_state": session_state or {}}
    si.workflow_session.session_data = sd
    si.workflow_session.db = db
    si.workflow_session.session_id = "test-session-123"
    si.previous_step_content = previous_step_content
    return si


def _make_verdict_json(
    reviewer: str = "Test Critic",
    verdict: str = "PASS",
    reasoning: str = "Looks good",
    blockers: list[str] | None = None,
    suggestions: list[str] | None = None,
) -> str:
    """Create a JSON verdict string."""
    return json.dumps({
        "reviewer": reviewer,
        "verdict": verdict,
        "reasoning": reasoning,
        "blockers": blockers or [],
        "suggestions": suggestions or [],
    })


def _make_all_pass_content() -> str:
    """Create content with 3 passing verdicts."""
    verdicts = [
        _make_verdict_json("Feasibility Critic", "PASS", "Plan is feasible"),
        _make_verdict_json("Completeness Critic", "PASS", "Plan is complete"),
        _make_verdict_json("Scope Critic", "PASS", "Scope is appropriate"),
    ]
    return "\n\n".join(f"```json\n{v}\n```" for v in verdicts)


def _make_mixed_content() -> str:
    """Create content with 2 PASS and 1 FAIL."""
    verdicts = [
        _make_verdict_json("Feasibility Critic", "PASS", "Plan is feasible"),
        _make_verdict_json(
            "Completeness Critic", "FAIL", "Missing edge cases",
            blockers=["No handling for timeouts"],
        ),
        _make_verdict_json("Scope Critic", "PASS", "Scope is appropriate"),
    ]
    return "\n\n".join(f"```json\n{v}\n```" for v in verdicts)


# ---------------------------------------------------------------------------
# GateVerdict model
# ---------------------------------------------------------------------------


class TestGateVerdict:
    def test_create_pass_verdict(self) -> None:
        v = GateVerdict(reviewer="Test", verdict="PASS")
        assert v.reviewer == "Test"
        assert v.verdict == "PASS"
        assert v.reasoning == ""
        assert v.blockers == []
        assert v.suggestions == []

    def test_create_fail_verdict_with_details(self) -> None:
        v = GateVerdict(
            reviewer="Critic",
            verdict="FAIL",
            reasoning="Plan has gaps",
            blockers=["Missing handling", "No rollback plan"],
            suggestions=["Add retry logic"],
        )
        assert v.verdict == "FAIL"
        assert len(v.blockers) == 2
        assert len(v.suggestions) == 1

    def test_serialization_roundtrip(self) -> None:
        v = GateVerdict(
            reviewer="Test",
            verdict="PASS",
            reasoning="OK",
            blockers=["b1"],
            suggestions=["s1"],
        )
        data = v.model_dump()
        v2 = GateVerdict(**data)
        assert v == v2

    def test_json_serialization(self) -> None:
        v = GateVerdict(reviewer="Test", verdict="PASS")
        json_str = v.model_dump_json()
        assert "Test" in json_str
        assert "PASS" in json_str


# ---------------------------------------------------------------------------
# parse_verdicts
# ---------------------------------------------------------------------------


class TestParseVerdicts:
    def test_parse_fenced_json(self) -> None:
        content = '```json\n{"reviewer": "A", "verdict": "PASS"}\n```'
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 1
        assert verdicts[0].reviewer == "A"
        assert verdicts[0].verdict == "PASS"

    def test_parse_raw_json(self) -> None:
        content = 'Here is my verdict: {"reviewer": "B", "verdict": "FAIL", "reasoning": "Bad plan"}'
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "FAIL"

    def test_parse_multiple_verdicts(self) -> None:
        content = _make_all_pass_content()
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 3

    def test_parse_mixed_verdicts(self) -> None:
        content = _make_mixed_content()
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 3
        pass_count = sum(1 for v in verdicts if v.verdict == "PASS")
        fail_count = sum(1 for v in verdicts if v.verdict == "FAIL")
        assert pass_count == 2
        assert fail_count == 1

    def test_deduplicates_by_reviewer(self) -> None:
        v = _make_verdict_json("Same Critic", "PASS")
        content = f"```json\n{v}\n```\n\n```json\n{v}\n```"
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 1

    def test_raises_on_empty_content(self) -> None:
        with pytest.raises(ValueError, match="Empty content"):
            parse_verdicts("")

    def test_raises_on_no_verdicts(self) -> None:
        with pytest.raises(ValueError, match="No valid GateVerdict"):
            parse_verdicts("This is just regular text with no JSON")

    def test_raises_on_json_without_verdict_field(self) -> None:
        with pytest.raises(ValueError, match="No valid GateVerdict"):
            parse_verdicts('```json\n{"reviewer": "A", "score": 5}\n```')

    def test_ignores_malformed_json(self) -> None:
        content = (
            '```json\n{broken json}\n```\n\n'
            '```json\n{"reviewer": "A", "verdict": "PASS"}\n```'
        )
        verdicts = parse_verdicts(content)
        assert len(verdicts) == 1

    def test_parse_with_blockers_and_suggestions(self) -> None:
        v = _make_verdict_json(
            "Critic", "FAIL", "Issues found",
            blockers=["b1", "b2"], suggestions=["s1"],
        )
        content = f"```json\n{v}\n```"
        verdicts = parse_verdicts(content)
        assert verdicts[0].blockers == ["b1", "b2"]
        assert verdicts[0].suggestions == ["s1"]


# ---------------------------------------------------------------------------
# format_feedback
# ---------------------------------------------------------------------------


class TestFormatFeedback:
    def test_formats_pass_verdict(self) -> None:
        v = GateVerdict(reviewer="Test", verdict="PASS", reasoning="All good")
        result = format_feedback([v])
        assert "PASS" in result
        assert "Test" in result
        assert "1/1 critics passed" in result

    def test_formats_fail_verdict(self) -> None:
        v = GateVerdict(
            reviewer="Critic",
            verdict="FAIL",
            reasoning="Bad",
            blockers=["Missing X"],
        )
        result = format_feedback([v])
        assert "FAIL" in result
        assert "Missing X" in result
        assert "0/1 critics passed" in result

    def test_formats_mixed_verdicts(self) -> None:
        verdicts = [
            GateVerdict(reviewer="A", verdict="PASS"),
            GateVerdict(reviewer="B", verdict="FAIL", blockers=["Issue"]),
        ]
        result = format_feedback(verdicts)
        assert "1/2 critics passed" in result

    def test_includes_suggestions(self) -> None:
        v = GateVerdict(
            reviewer="Critic",
            verdict="PASS",
            suggestions=["Consider adding X"],
        )
        result = format_feedback([v])
        assert "Consider adding X" in result


# ---------------------------------------------------------------------------
# check_plan_gate
# ---------------------------------------------------------------------------


class TestCheckPlanGate:
    def test_all_pass_sets_gate_passed(self) -> None:
        content = _make_all_pass_content()
        si = _make_step_input(previous_step_content=content)
        result = check_plan_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["plan_gate_passed"] is True
        assert result.content == "GATE_PASSED"

    def test_fail_sets_gate_not_passed(self) -> None:
        content = _make_mixed_content()
        si = _make_step_input(previous_step_content=content)
        result = check_plan_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["plan_gate_passed"] is False

    def test_increments_round_number(self) -> None:
        content = _make_all_pass_content()
        si = _make_step_input(
            previous_step_content=content,
            session_state={"plan_review_round": 1},
        )
        check_plan_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["plan_review_round"] == 2

    def test_first_round_starts_at_1(self) -> None:
        content = _make_all_pass_content()
        si = _make_step_input(previous_step_content=content)
        check_plan_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["plan_review_round"] == 1

    def test_stores_verdicts_on_fail(self) -> None:
        content = _make_mixed_content()
        si = _make_step_input(previous_step_content=content)
        check_plan_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert "plan_gate_verdicts" in ss
        assert len(ss["plan_gate_verdicts"]) == 3

    def test_fail_content_is_formatted_feedback(self) -> None:
        content = _make_mixed_content()
        si = _make_step_input(previous_step_content=content)
        result = check_plan_gate(si)
        assert "Review Feedback" in result.content
        assert "FAIL" in result.content

    def test_detects_team_member_errors_ac06(self) -> None:
        """AC-06: check_team_member_errors must catch error signals."""
        error_content = (
            "member agent-1 failed during execution. "
            "Traceback follows: some stack trace. " + "x" * 50
        )
        si = _make_step_input(previous_step_content=error_content)
        from orchestra.utils.team import TeamMemberError
        with pytest.raises(TeamMemberError):
            check_plan_gate(si)


# ---------------------------------------------------------------------------
# plan_review_approved (end_condition -- AC-01)
# ---------------------------------------------------------------------------


class TestPlanReviewApproved:
    def test_returns_true_when_gate_passed(self) -> None:
        outputs = [
            StepOutput(content="some text"),
            StepOutput(content="GATE_PASSED"),
        ]
        assert plan_review_approved(outputs) is True

    def test_returns_false_when_no_gate_passed(self) -> None:
        outputs = [
            StepOutput(content="feedback with review results"),
        ]
        assert plan_review_approved(outputs) is False

    def test_returns_false_on_empty_outputs(self) -> None:
        assert plan_review_approved([]) is False

    def test_returns_false_when_content_not_gate_passed(self) -> None:
        outputs = [StepOutput(content="some other text")]
        assert plan_review_approved(outputs) is False


# ---------------------------------------------------------------------------
# check_plan_review_result (post-Loop -- AC-04)
# ---------------------------------------------------------------------------


class TestCheckPlanReviewResult:
    def test_passed_returns_success(self) -> None:
        si = _make_step_input(
            session_state={"plan_gate_passed": True},
        )
        result = check_plan_review_result(si)
        assert result.content == "PLAN_REVIEW_PASSED"

    def test_failed_creates_decision_gate(self) -> None:
        verdicts_data = [
            {"reviewer": "A", "verdict": "FAIL", "reasoning": "Bad",
             "blockers": ["b1"], "suggestions": []},
        ]
        db = MagicMock()
        si = _make_step_input(
            session_state={
                "plan_gate_passed": False,
                "plan_gate_verdicts": verdicts_data,
                "plan_review_round": 3,
            },
            db=db,
        )
        with patch("orchestra.workflow.plan_review.create_decision_gate") as mock_gate:
            mock_gate.return_value = MagicMock(id="dg-test123")
            result = check_plan_review_result(si)

            mock_gate.assert_called_once()
            call_kwargs = mock_gate.call_args
            assert call_kwargs.kwargs["gate_type"] == "plan_review"
            assert call_kwargs.kwargs["workflow_run_id"] == "test-session-123"

        assert "PLAN_REVIEW_FAILED_AFTER_3_ROUNDS" in result.content
        ss = si.workflow_session.session_data["session_state"]
        assert ss["pending_decision_gate_id"] == "dg-test123"

    def test_failed_without_db_skips_gate_creation(self) -> None:
        si = _make_step_input(
            session_state={
                "plan_gate_passed": False,
                "plan_gate_verdicts": [],
                "plan_review_round": 2,
            },
            db=None,
        )
        result = check_plan_review_result(si)
        assert "PLAN_REVIEW_FAILED_AFTER_2_ROUNDS" in result.content


# ---------------------------------------------------------------------------
# revise_plan_from_feedback
# ---------------------------------------------------------------------------


class TestRevisePlanFromFeedback:
    def test_combines_plan_and_feedback(self) -> None:
        si = _make_step_input(
            previous_step_content="Missing handling for X",
            session_state={
                "latest_design_content": "Original plan content",
                "plan_review_round": 1,
            },
        )
        result = revise_plan_from_feedback(si)
        assert "Original plan content" in result.content
        assert "Missing handling for X" in result.content
        assert "Plan Revision Request" in result.content

    def test_updates_session_state(self) -> None:
        si = _make_step_input(
            previous_step_content="Fix these issues",
            session_state={
                "latest_design_content": "Old plan",
                "plan_review_round": 2,
            },
        )
        revise_plan_from_feedback(si)
        ss = si.workflow_session.session_data["session_state"]
        assert "Fix these issues" in ss["latest_design_content"]

    def test_raises_without_existing_plan(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={},
        )
        with pytest.raises(ValueError, match="No plan found"):
            revise_plan_from_feedback(si)

    def test_includes_round_number(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={
                "latest_design_content": "Plan",
                "plan_review_round": 3,
            },
        )
        result = revise_plan_from_feedback(si)
        assert "Round 3" in result.content
