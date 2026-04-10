"""Tests for P1-05: Design Review Gate (DESIGN.md §4.4).

Covers:
- create_design_review_team: 5 domain experts, TeamMode.broadcast
- check_design_gate: ALL APPROVE required, writes approved_design (sole write point)
- design_review_approved: end_condition for review loop (AC-01)
- create_check_design_review_result: closure factory for post-Loop decision gate (AC-04)
- revise_design_from_review: revision prompt generation
- _is_genuine_team_error: false-positive filtering

GateVerdict, parse_verdicts(), format_feedback() are from P1-04 (plan_review.py)
and tested in test_plan_review.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agno.workflow.types import StepOutput

from orchestra.workflow.design_review import (
    _is_genuine_team_error,
    check_design_gate,
    create_check_design_review_result,
    design_review_approved,
    revise_design_from_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step_input(
    previous_step_content: str | None = "",
    session_state: dict[str, Any] | None = None,
    db: Any = None,
) -> MagicMock:
    """Build a mock StepInput with a working session_state."""
    si = MagicMock()
    sd: dict[str, Any] = {"session_state": session_state or {}}
    si.workflow_session.session_data = sd
    si.previous_step_content = previous_step_content
    if db is not None:
        si.workflow_session.db = db
    si.workflow_session.session_id = "test-session-123"
    return si


def _make_verdict_json(reviewer: str, verdict: str) -> str:
    """Build a JSON verdict string."""
    import json
    return json.dumps({
        "reviewer": reviewer,
        "verdict": verdict,
        "reasoning": f"{reviewer} reasoning",
        "blockers": [] if verdict == "APPROVED" else [f"{reviewer} blocker"],
        "suggestions": [],
    })


def _all_approved_content() -> str:
    """Build broadcast output where all 5 experts approve."""
    verdicts = [
        _make_verdict_json("Product Manager", "APPROVED"),
        _make_verdict_json("Architect Reviewer", "APPROVED"),
        _make_verdict_json("Security Expert", "APPROVED"),
        _make_verdict_json("UX Expert", "APPROVED"),
        _make_verdict_json("QA Expert", "APPROVED"),
    ]
    return "\n".join(f"```json\n{v}\n```" for v in verdicts)


def _mixed_verdict_content() -> str:
    """Build broadcast output where some reject."""
    verdicts = [
        _make_verdict_json("Product Manager", "APPROVED"),
        _make_verdict_json("Architect Reviewer", "REJECTED"),
        _make_verdict_json("Security Expert", "APPROVED"),
        _make_verdict_json("UX Expert", "REJECTED"),
        _make_verdict_json("QA Expert", "APPROVED"),
    ]
    return "\n".join(f"```json\n{v}\n```" for v in verdicts)


# ---------------------------------------------------------------------------
# _is_genuine_team_error
# ---------------------------------------------------------------------------


class TestIsGenuineTeamError:
    def test_traceback_is_genuine(self) -> None:
        errors = ["Traceback (most recent call last): File test.py"]
        assert _is_genuine_team_error(errors) is True

    def test_member_failed_is_genuine(self) -> None:
        errors = ["member QA Expert failed during execution"]
        assert _is_genuine_team_error(errors) is True

    def test_bare_error_not_genuine(self) -> None:
        errors = ["Error handling needs improvement in the design"]
        assert _is_genuine_team_error(errors) is False

    def test_empty_not_genuine(self) -> None:
        assert _is_genuine_team_error([]) is False


# ---------------------------------------------------------------------------
# check_design_gate
# ---------------------------------------------------------------------------


class TestCheckDesignGate:
    def test_all_approved_writes_approved_design(self) -> None:
        """DESIGN.md §4.4: check_design_gate is the ONLY write point for approved_design."""
        si = _make_step_input(
            previous_step_content=_all_approved_content(),
            session_state={"latest_design_content": "The final design document"},
        )
        result = check_design_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["approved_design"] == "The final design document"
        assert ss["design_gate_passed"] is True
        assert result.content == "GATE_PASSED"

    def test_all_approved_increments_round(self) -> None:
        si = _make_step_input(
            previous_step_content=_all_approved_content(),
            session_state={"latest_design_content": "design", "design_review_round": 1},
        )
        check_design_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["design_review_round"] == 2

    def test_first_round_starts_at_1(self) -> None:
        si = _make_step_input(
            previous_step_content=_all_approved_content(),
            session_state={"latest_design_content": "design"},
        )
        check_design_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["design_review_round"] == 1

    def test_rejection_sets_gate_not_passed(self) -> None:
        si = _make_step_input(
            previous_step_content=_mixed_verdict_content(),
            session_state={"latest_design_content": "design"},
        )
        result = check_design_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert ss["design_gate_passed"] is False
        assert "approved_design" not in ss
        assert "GATE_PASSED" not in result.content

    def test_rejection_stores_verdicts(self) -> None:
        si = _make_step_input(
            previous_step_content=_mixed_verdict_content(),
            session_state={"latest_design_content": "design"},
        )
        check_design_gate(si)
        ss = si.workflow_session.session_data["session_state"]
        assert len(ss["design_gate_verdicts"]) == 5

    def test_rejection_returns_formatted_feedback(self) -> None:
        si = _make_step_input(
            previous_step_content=_mixed_verdict_content(),
            session_state={"latest_design_content": "design"},
        )
        result = check_design_gate(si)
        assert "Review Feedback" in result.content
        assert "REJECTED" in result.content

    def test_detects_genuine_team_errors_ac06(self) -> None:
        error_content = (
            "Expert output. Traceback (most recent call last): "
            "File agent.py, line 42. " + "x" * 100
        )
        si = _make_step_input(previous_step_content=error_content)
        from orchestra.utils.team import TeamMemberError
        with pytest.raises(TeamMemberError):
            check_design_gate(si)

    def test_allows_legitimate_error_discussion(self) -> None:
        """Experts discussing error handling should not trigger AC-06."""
        content = _all_approved_content()
        si = _make_step_input(
            previous_step_content=content,
            session_state={"latest_design_content": "design"},
        )
        result = check_design_gate(si)
        assert result.content == "GATE_PASSED"


# ---------------------------------------------------------------------------
# design_review_approved (AC-01)
# ---------------------------------------------------------------------------


class TestDesignReviewApproved:
    def test_returns_true_when_gate_passed(self) -> None:
        outputs = [
            StepOutput(content="some content"),
            StepOutput(content="GATE_PASSED"),
        ]
        assert design_review_approved(outputs) is True

    def test_returns_false_when_no_gate_passed(self) -> None:
        outputs = [
            StepOutput(content="Review Feedback\n..."),
            StepOutput(content="more feedback"),
        ]
        assert design_review_approved(outputs) is False

    def test_returns_false_on_empty(self) -> None:
        assert design_review_approved([]) is False

    def test_returns_false_on_partial_match(self) -> None:
        outputs = [StepOutput(content="GATE_PASSED_EXTRA")]
        assert design_review_approved(outputs) is False


# ---------------------------------------------------------------------------
# create_check_design_review_result (AC-04, closure)
# ---------------------------------------------------------------------------


class TestCheckDesignReviewResult:
    def test_factory_returns_callable(self) -> None:
        events_db = MagicMock()
        step_fn = create_check_design_review_result(events_db)
        assert callable(step_fn)

    def test_passed_returns_success(self) -> None:
        events_db = MagicMock()
        step_fn = create_check_design_review_result(events_db)
        si = _make_step_input(
            session_state={"design_gate_passed": True},
        )
        result = step_fn(si)
        assert result.content == "DESIGN_REVIEW_PASSED"

    def test_failed_creates_gate_with_events_db(self) -> None:
        """Verify DecisionGate uses events_db (from closure), not traces_db."""
        events_db = MagicMock()
        step_fn = create_check_design_review_result(events_db)
        verdicts_data = [
            {"reviewer": "Architect", "verdict": "REJECTED",
             "reasoning": "Bad", "blockers": ["b1"], "suggestions": []},
        ]
        si = _make_step_input(
            session_state={
                "design_gate_passed": False,
                "design_gate_verdicts": verdicts_data,
                "design_review_round": 2,
            },
            db=MagicMock(),  # traces_db — should NOT be used
        )
        with patch("orchestra.workflow.design_review.create_decision_gate") as mock_gate:
            mock_gate.return_value = MagicMock(id="dg-design-123")
            result = step_fn(si)

            mock_gate.assert_called_once()
            assert mock_gate.call_args[0][0] is events_db
            assert mock_gate.call_args.kwargs["gate_type"] == "design_review"

        assert "DESIGN_REVIEW_FAILED_AFTER_2_ROUNDS" in result.content
        ss = si.workflow_session.session_data["session_state"]
        assert ss["pending_decision_gate_id"] == "dg-design-123"


# ---------------------------------------------------------------------------
# revise_design_from_review
# ---------------------------------------------------------------------------


class TestReviseDesignFromReview:
    def test_combines_design_and_feedback(self) -> None:
        si = _make_step_input(
            previous_step_content="Security Expert: REJECTED. Missing auth.",
            session_state={
                "latest_design_content": "Original design",
                "design_review_round": 1,
            },
        )
        result = revise_design_from_review(si)
        assert "Original design" in result.content
        assert "Missing auth" in result.content
        assert "Design Revision Request" in result.content
        assert "Round 1" in result.content

    def test_updates_session_state(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={
                "latest_design_content": "Original",
                "design_review_round": 2,
            },
        )
        revise_design_from_review(si)
        ss = si.workflow_session.session_data["session_state"]
        assert "Feedback" in ss["latest_design_content"]

    def test_raises_without_existing_design(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={},
        )
        with pytest.raises(ValueError, match="No design found"):
            revise_design_from_review(si)

    def test_raises_with_empty_design(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={"latest_design_content": ""},
        )
        with pytest.raises(ValueError, match="No design found"):
            revise_design_from_review(si)

    def test_includes_round_number(self) -> None:
        si = _make_step_input(
            previous_step_content="Feedback",
            session_state={
                "latest_design_content": "Design",
                "design_review_round": 3,
            },
        )
        result = revise_design_from_review(si)
        assert "Round 3" in result.content
