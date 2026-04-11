"""Quality Gates for the VALIDATE phase (P1-10, DESIGN.md §2.6).

Provides `run_quality_gates(work_unit)` that runs test, lint, and typecheck
checks. Returns a QualityGateResult. Used in both per-unit VALIDATE phase
and integration review.

Failure is blocking — causes the 4-phase loop to retry (not skip).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateResult:
    """Result of a single quality gate check.

    Attributes:
        gate_name: Name of the gate (e.g. "test", "lint", "typecheck").
        passed: Whether the gate passed.
        output: Captured stdout/stderr from the gate command.
        return_code: Process return code (0 = success).
    """

    gate_name: str
    passed: bool
    output: str
    return_code: int


@dataclass(frozen=True)
class QualityGateResult:
    """Aggregate result of all quality gates.

    Attributes:
        passed: True only if ALL gates passed.
        results: Individual gate results.
        summary: Human-readable summary of pass/fail status.
    """

    passed: bool
    results: list[GateResult]
    summary: str

    def __bool__(self) -> bool:
        """Allow truthiness checks: ``if not run_quality_gates(...): retry``."""
        return self.passed


@dataclass(frozen=True)
class GateConfig:
    """Configuration for a single quality gate.

    Attributes:
        name: Gate name (e.g. "test", "lint", "typecheck").
        command: Command to execute. Parsed via shlex.split() when shell=False.
        timeout_seconds: Maximum execution time before the gate is considered failed.
        working_dir: Working directory for command execution. None = cwd.
        shell: If True (default), run command through the shell. If False,
            use shlex.split() for safer execution without shell interpolation.
    """

    name: str
    command: str
    timeout_seconds: int = 300
    working_dir: str | None = None
    shell: bool = True


# Default gate configurations matching DESIGN.md §2.6
DEFAULT_GATES: list[GateConfig] = [
    GateConfig(name="test", command="uv run pytest --tb=short -q", timeout_seconds=300),
    GateConfig(name="lint", command="uv run ruff check .", timeout_seconds=60),
    GateConfig(name="typecheck", command="uv run pyright", timeout_seconds=120),
]


def run_single_gate(
    gate: GateConfig,
    *,
    working_dir: str | None = None,
    env: dict[str, str] | None = None,
) -> GateResult:
    """Execute a single quality gate command.

    Args:
        gate: Gate configuration.
        working_dir: Override working directory (falls back to gate.working_dir).
        env: Optional environment variables merged with os.environ for the
            subprocess. Overrides specific variables without losing PATH etc.

    Returns:
        GateResult with pass/fail status and captured output.
    """
    cwd = working_dir or gate.working_dir
    merged_env = {**os.environ, **env} if env else None
    cmd: str | list[str] = gate.command if gate.shell else shlex.split(gate.command)
    try:
        result = subprocess.run(
            cmd,
            shell=gate.shell,
            capture_output=True,
            text=True,
            timeout=gate.timeout_seconds,
            cwd=cwd,
            env=merged_env,
        )
        passed = result.returncode == 0
        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        return GateResult(
            gate_name=gate.name,
            passed=passed,
            output=output.strip(),
            return_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            gate_name=gate.name,
            passed=False,
            output=f"Gate '{gate.name}' timed out after {gate.timeout_seconds}s",
            return_code=-1,
        )
    except OSError as e:
        return GateResult(
            gate_name=gate.name,
            passed=False,
            output=f"Gate '{gate.name}' failed to execute: {e}",
            return_code=-2,
        )


def run_quality_gates(
    work_unit: Any = None,
    *,
    gates: list[GateConfig] | None = None,
    working_dir: str | None = None,
    env: dict[str, str] | None = None,
    fail_fast: bool = False,
) -> QualityGateResult:
    """Run all quality gates and return aggregate result.

    Returns True (via .passed) only if ALL gates pass. Failure is blocking —
    the 4-phase loop should retry on failure, not skip.

    Args:
        work_unit: Optional WorkUnit context (for future file-scoped gating).
        gates: List of gate configurations. Defaults to DEFAULT_GATES.
        working_dir: Working directory for all gate commands.
        env: Optional environment variables for subprocesses.
        fail_fast: If True, stop after the first failing gate.

    Returns:
        QualityGateResult with aggregate pass/fail and per-gate results.

    Raises:
        ValueError: If an explicit empty gate list is passed. Quality gates
            are mandatory — an empty list would silently bypass validation.
    """
    active_gates = gates if gates is not None else DEFAULT_GATES

    if not active_gates:
        raise ValueError(
            "No quality gates configured. Quality gates are mandatory — "
            "an empty gate list would silently bypass validation. "
            "Use DEFAULT_GATES or provide an explicit gate list."
        )

    results: list[GateResult] = []

    for gate in active_gates:
        result = run_single_gate(gate, working_dir=working_dir, env=env)
        results.append(result)

        if not result.passed and fail_fast:
            break

    all_passed = all(r.passed for r in results)
    failed_gates = [r.gate_name for r in results if not r.passed]
    passed_gates = [r.gate_name for r in results if r.passed]

    if all_passed:
        summary = f"All {len(results)} quality gates passed: {', '.join(passed_gates)}"
    else:
        summary = (
            f"{len(failed_gates)} of {len(active_gates)} quality gates failed: "
            f"{', '.join(failed_gates)}"
        )

    return QualityGateResult(
        passed=all_passed,
        results=results,
        summary=summary,
    )
