"""Tests for orchestra.workflow.quality_gates (P1-10).

Tests cover:
- All gates passing
- Each gate failing independently
- Multiple failures
- Configurable gate list
- Timeout handling
- Command execution errors
- fail_fast mode
- Empty gate list (raises ValueError)
"""

import pytest

from orchestra.workflow.quality_gates import (
    DEFAULT_GATES,
    GateConfig,
    GateResult,
    QualityGateResult,
    run_quality_gates,
    run_single_gate,
)


class TestGateConfig:
    def test_default_timeout(self):
        gate = GateConfig(name="test", command="echo ok")
        assert gate.timeout_seconds == 300

    def test_custom_timeout(self):
        gate = GateConfig(name="test", command="echo ok", timeout_seconds=60)
        assert gate.timeout_seconds == 60

    def test_frozen(self):
        gate = GateConfig(name="test", command="echo ok")
        with pytest.raises(AttributeError):
            gate.name = "changed"  # type: ignore[misc]


class TestDefaultGates:
    def test_has_three_gates(self):
        assert len(DEFAULT_GATES) == 3

    def test_gate_names(self):
        names = [g.name for g in DEFAULT_GATES]
        assert "test" in names
        assert "lint" in names
        assert "typecheck" in names

    def test_all_have_commands(self):
        for gate in DEFAULT_GATES:
            assert len(gate.command) > 0


class TestRunSingleGate:
    def test_passing_command(self):
        gate = GateConfig(name="echo", command="echo hello")
        result = run_single_gate(gate)
        assert result.passed is True
        assert result.return_code == 0
        assert "hello" in result.output

    def test_failing_command(self):
        gate = GateConfig(name="fail", command="exit 1")
        result = run_single_gate(gate)
        assert result.passed is False
        assert result.return_code == 1

    def test_nonzero_exit_code(self):
        gate = GateConfig(name="fail42", command="exit 42")
        result = run_single_gate(gate)
        assert result.passed is False
        assert result.return_code == 42

    def test_captures_stdout(self):
        gate = GateConfig(name="out", command="echo test_output")
        result = run_single_gate(gate)
        assert "test_output" in result.output

    def test_captures_stderr(self):
        gate = GateConfig(name="err", command="echo error_msg >&2")
        result = run_single_gate(gate)
        assert "error_msg" in result.output

    def test_timeout(self):
        gate = GateConfig(name="slow", command="sleep 10", timeout_seconds=1)
        result = run_single_gate(gate)
        assert result.passed is False
        assert result.return_code == -1
        assert "timed out" in result.output

    def test_invalid_command(self):
        gate = GateConfig(
            name="bad",
            command="/nonexistent/binary/path_that_does_not_exist_xyz",
        )
        result = run_single_gate(gate)
        assert result.passed is False

    def test_working_dir(self, tmp_path):
        gate = GateConfig(name="pwd", command="pwd")
        result = run_single_gate(gate, working_dir=str(tmp_path))
        assert str(tmp_path) in result.output

    def test_result_is_frozen(self):
        gate = GateConfig(name="echo", command="echo ok")
        result = run_single_gate(gate)
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]


class TestRunQualityGates:
    def test_all_pass(self):
        gates = [
            GateConfig(name="g1", command="echo pass1"),
            GateConfig(name="g2", command="echo pass2"),
            GateConfig(name="g3", command="echo pass3"),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is True
        assert len(result.results) == 3
        assert all(r.passed for r in result.results)
        assert "All 3" in result.summary

    def test_first_gate_fails(self):
        gates = [
            GateConfig(name="fail", command="exit 1"),
            GateConfig(name="pass", command="echo ok"),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is False
        assert "fail" in result.summary

    def test_last_gate_fails(self):
        gates = [
            GateConfig(name="pass", command="echo ok"),
            GateConfig(name="fail", command="exit 1"),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is False

    def test_middle_gate_fails(self):
        gates = [
            GateConfig(name="g1", command="echo ok"),
            GateConfig(name="g2", command="exit 1"),
            GateConfig(name="g3", command="echo ok"),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is False
        assert result.results[0].passed is True
        assert result.results[1].passed is False
        assert result.results[2].passed is True

    def test_multiple_failures(self):
        gates = [
            GateConfig(name="f1", command="exit 1"),
            GateConfig(name="f2", command="exit 2"),
            GateConfig(name="p1", command="echo ok"),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is False
        assert "2 of 3" in result.summary

    def test_empty_gate_list_raises(self):
        """Empty gate list is a configuration error, not a silent pass."""
        with pytest.raises(ValueError, match="No quality gates configured"):
            run_quality_gates(gates=[])

    def test_fail_fast_stops_early(self):
        gates = [
            GateConfig(name="fail", command="exit 1"),
            GateConfig(name="never_runs", command="echo should_not_run"),
        ]
        result = run_quality_gates(gates=gates, fail_fast=True)
        assert result.passed is False
        assert len(result.results) == 1
        assert result.results[0].gate_name == "fail"

    def test_fail_fast_all_pass(self):
        gates = [
            GateConfig(name="g1", command="echo ok"),
            GateConfig(name="g2", command="echo ok"),
        ]
        result = run_quality_gates(gates=gates, fail_fast=True)
        assert result.passed is True
        assert len(result.results) == 2

    def test_custom_working_dir(self, tmp_path):
        gates = [GateConfig(name="pwd", command="pwd")]
        result = run_quality_gates(gates=gates, working_dir=str(tmp_path))
        assert result.passed is True
        assert str(tmp_path) in result.results[0].output

    def test_result_is_frozen(self):
        gates = [GateConfig(name="g1", command="echo ok")]
        result = run_quality_gates(gates=gates)
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]

    def test_summary_lists_failed_gates(self):
        gates = [
            GateConfig(name="test", command="exit 1"),
            GateConfig(name="lint", command="echo ok"),
            GateConfig(name="typecheck", command="exit 1"),
        ]
        result = run_quality_gates(gates=gates)
        assert "test" in result.summary
        assert "typecheck" in result.summary

    def test_timeout_counts_as_failure(self):
        gates = [
            GateConfig(name="slow", command="sleep 10", timeout_seconds=1),
        ]
        result = run_quality_gates(gates=gates)
        assert result.passed is False
        assert result.results[0].return_code == -1

    def test_work_unit_param_accepted(self):
        """work_unit parameter is accepted for future file-scoped gating."""
        gates = [GateConfig(name="g1", command="echo ok")]
        result = run_quality_gates(work_unit={"id": "wu-1"}, gates=gates)
        assert result.passed is True
