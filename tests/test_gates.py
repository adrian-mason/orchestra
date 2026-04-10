"""Tests for orchestra.utils.gates (AC-02, AC-04)."""

import pytest

from agno.workflow.types import OnError, OnReject, StepInput, StepOutput

from orchestra.utils.gates import create_gate_check_step, create_decision_gate_step


def _noop_executor(si: StepInput) -> StepOutput:
    return StepOutput(content="ok")


class TestCreateGateCheckStep:
    def test_creates_step_with_on_error_fail(self):
        step = create_gate_check_step("test_gate", _noop_executor)
        assert step.name == "test_gate"
        assert step.on_error == OnError.fail

    def test_allows_explicit_on_error_fail(self):
        step = create_gate_check_step(
            "test_gate", _noop_executor, on_error=OnError.fail
        )
        assert step.on_error == OnError.fail

    def test_rejects_on_error_skip(self):
        with pytest.raises(ValueError, match="AC-02"):
            create_gate_check_step(
                "test_gate", _noop_executor, on_error=OnError.skip
            )

    def test_does_not_set_requires_confirmation(self):
        step = create_gate_check_step("test_gate", _noop_executor)
        assert step.requires_confirmation is False

    def test_rejects_requires_confirmation_true(self):
        with pytest.raises(ValueError, match="AC-04"):
            create_gate_check_step(
                "test_gate", _noop_executor, requires_confirmation=True
            )

    def test_rejects_on_reject(self):
        with pytest.raises(ValueError, match="AC-04"):
            create_gate_check_step(
                "test_gate", _noop_executor, on_reject=OnReject.cancel
            )


class TestCreateDecisionGateStep:
    def test_creates_step_with_all_enforced_params(self):
        step = create_decision_gate_step("decision", _noop_executor)
        assert step.name == "decision"
        assert step.on_error == OnError.fail
        assert step.requires_confirmation is True
        assert step.on_reject == OnReject.cancel

    def test_allows_explicit_correct_values(self):
        step = create_decision_gate_step(
            "decision",
            _noop_executor,
            on_error=OnError.fail,
            requires_confirmation=True,
            on_reject=OnReject.cancel,
        )
        assert step.on_error == OnError.fail

    def test_rejects_on_error_skip(self):
        with pytest.raises(ValueError, match="AC-02/AC-04"):
            create_decision_gate_step(
                "decision", _noop_executor, on_error=OnError.skip
            )

    def test_rejects_requires_confirmation_false(self):
        with pytest.raises(ValueError, match="AC-02/AC-04"):
            create_decision_gate_step(
                "decision", _noop_executor, requires_confirmation=False
            )

    def test_rejects_on_reject_skip(self):
        with pytest.raises(ValueError, match="AC-02/AC-04"):
            create_decision_gate_step(
                "decision", _noop_executor, on_reject=OnReject.skip
            )
