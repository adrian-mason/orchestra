"""Per-Unit 4-Phase Loop and DAG-based execution (P1-08, DESIGN.md §2.5-§2.6).

Each WorkUnit goes through: IMPL → VALIDATE → ADV.REVIEW → COMMIT.
Max 3 attempts per unit; escalated after 3 failures.

``execute_work_units()`` reads work units from session_state (AC-03), builds
a DAG, and executes in topological batches. DESIGN.md §2.5 shows reading
from previous_step_content, but session_state is preferred for persistence
and AC-03 compliance.

Gate 0 Constraints:
- AC-01: Loop exit via end_condition callable, never StepOutput(stop=True)
- AC-03: Session state via get_ss()/set_ss() only
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agno.workflow.types import StepInput, StepOutput

from orchestra.model_resolver import (
    ModelsConfig,
    create_fresh_adversarial_reviewers,
    instantiate_model,
    resolve_model,
)
from orchestra.models.work_unit import WorkUnit
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import has_genuine_error
from orchestra.workflow.dag import WorkUnitDAG, build_dag
from orchestra.workflow.quality_gates import QualityGateResult, run_quality_gates

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

_JSON_VERDICT_RE = re.compile(
    r"\{[^{}]*\"verdict\"\s*:\s*\"[^\"]+\"[^{}]*\}",
    re.DOTALL,
)


@dataclass(frozen=True)
class PhaseResult:
    """Result of a single phase within the 4-phase loop."""

    phase: str
    success: bool
    output: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnitResult:
    """Result of executing a single WorkUnit through the 4-phase loop."""

    unit_id: str
    status: str  # "completed" | "escalated" | "blocked"
    attempts: int = 0
    phases: list[PhaseResult] = field(default_factory=list)
    assigned_model: str | None = None


def _parse_review_verdicts(content: str) -> list[dict[str, Any]]:
    """Extract structured verdict dicts from reviewer output.

    Finds JSON objects containing a "verdict" field. Returns a list of
    parsed dicts. This is a local implementation of the same pattern
    used by parse_verdicts() in plan_review — will be unified after
    P1-05 merges.
    """
    verdicts: list[dict[str, Any]] = []
    for match in _JSON_VERDICT_RE.finditer(content):
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "verdict" in data:
                verdicts.append(data)
        except json.JSONDecodeError:
            continue
    return verdicts


def _run_implement_phase(
    work_unit: WorkUnit,
    *,
    model_id: str,
) -> PhaseResult:
    """Phase 1: IMPLEMENT — run the implementer agent on the work unit.

    Returns the implementation output for downstream phases.
    Fails if agent returns None/empty content or execution errors.
    """
    from agno.agent import Agent

    impl_agent = Agent(
        name=f"Implementer-{work_unit.id}",
        model=instantiate_model(model_id),
        instructions=[
            f"Implement the following work unit.\n\n"
            f"## Title: {work_unit.title}\n\n"
            f"## Description\n{work_unit.description}\n\n"
            f"## Definition of Done\n"
            + "\n".join(f"- {item}" for item in work_unit.dod)
            + "\n\n## File Scope\n"
            + "\n".join(f"- {f}" for f in work_unit.file_scope)
        ],
    )
    result = impl_agent.run(work_unit.description)
    content = str(result.content or "")

    if not content.strip():
        return PhaseResult(
            phase="IMPLEMENT",
            success=False,
            output="Implementer returned empty content",
        )

    if has_genuine_error(content):
        return PhaseResult(
            phase="IMPLEMENT",
            success=False,
            output=content,
            details={"error": "Agent execution error detected"},
        )

    return PhaseResult(
        phase="IMPLEMENT",
        success=True,
        output=content,
    )


def _run_validate_phase(
    work_unit: WorkUnit,
    *,
    working_dir: str | None = None,
) -> PhaseResult:
    """Phase 2: VALIDATE — run quality gates (test + lint + typecheck).

    Uses run_quality_gates() from P1-10. Failure is blocking.
    """
    gate_result: QualityGateResult = run_quality_gates(
        work_unit=work_unit,
        working_dir=working_dir,
    )
    return PhaseResult(
        phase="VALIDATE",
        success=gate_result.passed,
        output=gate_result.summary,
        details={"gate_results": [
            {"name": r.gate_name, "passed": r.passed, "output": r.output[:500]}
            for r in gate_result.results
        ]},
    )


def _run_review_phase(
    work_unit: WorkUnit,
    impl_output: str,
    *,
    implementer_model_id: str,
) -> PhaseResult:
    """Phase 3: ADVERSARIAL REVIEW — cross-model review.

    Uses create_fresh_adversarial_reviewers() from P1-06.
    Verdicts are parsed via structured JSON extraction — substring
    matching is not acceptable for gate decisions.
    """
    reviewers = create_fresh_adversarial_reviewers(implementer_model_id)
    review_prompt = (
        f"Review the following implementation for work unit '{work_unit.title}'.\n\n"
        f"## Implementation Output\n{impl_output}\n\n"
        f"## Definition of Done\n"
        + "\n".join(f"- {item}" for item in work_unit.dod)
        + "\n\nReturn a JSON verdict:\n"
        '{"reviewer": "<your name>", "verdict": "APPROVED" or "REJECTED",\n'
        ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
    )

    all_content: list[str] = []
    for reviewer in reviewers:
        review = reviewer.run(review_prompt)
        all_content.append(str(review.content or ""))

    combined_content = "\n\n".join(all_content)

    if has_genuine_error(combined_content):
        return PhaseResult(
            phase="REVIEW",
            success=False,
            output=combined_content[:2000],
            details={"error": "Reviewer agent execution error detected"},
        )

    verdicts = _parse_review_verdicts(combined_content)
    has_rejection = any(
        v.get("verdict") in ("REJECTED", "NEEDS_REVISION", "FAIL")
        for v in verdicts
    )
    if not verdicts:
        has_rejection = True

    return PhaseResult(
        phase="REVIEW",
        success=not has_rejection,
        output=combined_content[:2000],
        details={
            "reviewer_count": len(reviewers),
            "verdicts": verdicts,
        },
    )


def _run_commit_phase(work_unit: WorkUnit) -> PhaseResult:
    """Phase 4: COMMIT — mark work unit as committed.

    Actual git commit logic is deferred to integration — this phase
    records the completion state. Real commit operations will be
    added when the git integration layer is implemented.
    """
    return PhaseResult(
        phase="COMMIT",
        success=True,
        output=f"Work unit {work_unit.id} committed",
    )


def run_four_phase_loop(
    work_unit: WorkUnit,
    *,
    project: str | None = None,
    config: ModelsConfig | None = None,
    working_dir: str | None = None,
) -> UnitResult:
    """Execute a single WorkUnit through the 4-phase loop.

    IMPL → VALIDATE → ADV.REVIEW → COMMIT
    Max 3 attempts; status set to "escalated" after 3 failures.

    The implementer model is resolved via resolve_model(role="implementer").
    Adversarial reviewers use create_fresh_adversarial_reviewers() for
    cross-model diversity.

    Args:
        work_unit: The WorkUnit to execute.
        project: Optional project name for model resolution.
        config: Optional ModelsConfig for model resolution.
        working_dir: Working directory for quality gates.

    Returns:
        UnitResult with status "completed" or "escalated".
    """
    effective_config = config or ModelsConfig()
    impl_model_id = resolve_model(
        role="implementer",
        config=effective_config,
        project=project,
    )
    work_unit.assigned_model = impl_model_id

    unit_result = UnitResult(
        unit_id=work_unit.id,
        status="escalated",
        assigned_model=impl_model_id,
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        unit_result.attempts = attempt
        logger.info(
            "WorkUnit %s attempt %d/%d (model: %s)",
            work_unit.id, attempt, MAX_ATTEMPTS, impl_model_id,
        )

        # Phase 1: IMPLEMENT
        impl_result = _run_implement_phase(work_unit, model_id=impl_model_id)
        unit_result.phases.append(impl_result)
        if not impl_result.success:
            logger.warning("WorkUnit %s IMPLEMENT failed (attempt %d)", work_unit.id, attempt)
            continue

        # Phase 2: VALIDATE
        validate_result = _run_validate_phase(work_unit, working_dir=working_dir)
        unit_result.phases.append(validate_result)
        if not validate_result.success:
            logger.warning("WorkUnit %s VALIDATE failed (attempt %d)", work_unit.id, attempt)
            continue

        # Phase 3: ADVERSARIAL REVIEW
        review_result = _run_review_phase(
            work_unit, impl_result.output, implementer_model_id=impl_model_id,
        )
        unit_result.phases.append(review_result)
        if not review_result.success:
            logger.warning("WorkUnit %s REVIEW rejected (attempt %d)", work_unit.id, attempt)
            continue

        # Phase 4: COMMIT
        commit_result = _run_commit_phase(work_unit)
        unit_result.phases.append(commit_result)
        if commit_result.success:
            unit_result.status = "completed"
            logger.info("WorkUnit %s completed on attempt %d", work_unit.id, attempt)
            break

    if unit_result.status == "escalated":
        logger.warning(
            "WorkUnit %s escalated after %d attempts", work_unit.id, MAX_ATTEMPTS,
        )

    return unit_result


def _last_failed_phase(result: UnitResult) -> dict[str, Any] | None:
    """Extract the last failed phase from a UnitResult for serialization."""
    for phase in reversed(result.phases):
        if not phase.success:
            return {
                "phase": phase.phase,
                "output": phase.output[:500],
                "details": phase.details,
            }
    return None


def execute_work_units(step_input: StepInput) -> StepOutput:
    """Execute all work units in DAG order (DESIGN.md §2.5).

    Reads serialized work units from session_state, builds a DAG,
    and executes in topological batches. Units within a batch have
    no mutual dependencies and could run in parallel (currently
    sequential; parallel execution deferred to Phase 2).

    Units whose dependencies were escalated are marked "blocked" and
    skipped to avoid wasting API calls on inevitably failing units.

    Session state reads:
        - work_units: List of WorkUnit dicts (from P1-07 decomposition)
        - project_name: Optional project name for model resolution

    Session state writes:
        - execution_results: List of UnitResult dicts
        - escalated_units: List of unit IDs that failed after max attempts

    AC-03: All session state access via get_ss()/set_ss().
    """
    raw_units = get_ss(step_input, "work_units", [])
    if not raw_units:
        raise ValueError(
            "No work_units in session_state. "
            "decompose_work_units() must run before execute_work_units()."
        )

    work_units = [WorkUnit(**wu) for wu in raw_units]
    project = get_ss(step_input, "project_name")

    # Build DAG and execute in topological batches
    dag: WorkUnitDAG = build_dag(work_units)
    batches = dag.topological_batches()

    all_results: list[UnitResult] = []
    completed_ids: set[str] = set()
    failed_ids: set[str] = set()

    for batch_idx, batch in enumerate(batches):
        logger.info(
            "Executing batch %d/%d (%d units)",
            batch_idx + 1, len(batches), len(batch),
        )

        for wu in batch:
            blocked_deps = set(wu.dependencies) & failed_ids
            if blocked_deps:
                blocked_result = UnitResult(
                    unit_id=wu.id,
                    status="blocked",
                )
                all_results.append(blocked_result)
                failed_ids.add(wu.id)
                logger.warning(
                    "WorkUnit %s blocked — dependencies failed: %s",
                    wu.id, blocked_deps,
                )
                continue

            result = run_four_phase_loop(wu, project=project)
            all_results.append(result)
            if result.status == "completed":
                completed_ids.add(wu.id)
            else:
                failed_ids.add(wu.id)

    # Store results in session_state (include last failed phase for debugging)
    serialized_results = [
        {
            "unit_id": r.unit_id,
            "status": r.status,
            "attempts": r.attempts,
            "assigned_model": r.assigned_model,
            "last_failed_phase": _last_failed_phase(r),
        }
        for r in all_results
    ]
    set_ss(step_input, "execution_results", serialized_results)

    escalated = [
        r.unit_id for r in all_results if r.status in ("escalated", "blocked")
    ]
    set_ss(step_input, "escalated_units", escalated)

    completed_count = len(completed_ids)
    total_count = len(work_units)
    summary = f"{completed_count}/{total_count} work units completed"
    if escalated:
        summary += f", {len(escalated)} escalated: {', '.join(escalated)}"

    logger.info("Execution complete: %s", summary)
    return StepOutput(content=json.dumps(serialized_results))
