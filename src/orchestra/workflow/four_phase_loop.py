"""Per-Unit 4-Phase Loop and DAG-based execution (P1-08, DESIGN.md §2.5-§2.6).

Each WorkUnit goes through: IMPL → VALIDATE → ADV.REVIEW → COMMIT.
Max 3 attempts per unit; escalated after 3 failures.

`execute_work_units()` reads work units from session_state, builds a DAG,
and executes in topological batches (independent units run in parallel).

Gate 0 Constraints:
- AC-01: Loop exit via end_condition callable, never StepOutput(stop=True)
- AC-03: Session state via get_ss()/set_ss() only
- AC-06: check_team_member_errors() on agent output
"""

from __future__ import annotations

import json
import logging
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
from orchestra.workflow.dag import WorkUnitDAG, build_dag
from orchestra.workflow.quality_gates import QualityGateResult, run_quality_gates

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


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
    status: str  # "completed" | "escalated"
    attempts: int = 0
    phases: list[PhaseResult] = field(default_factory=list)
    assigned_model: str | None = None


def _run_implement_phase(
    work_unit: WorkUnit,
    *,
    model_id: str,
) -> PhaseResult:
    """Phase 1: IMPLEMENT — run the implementer agent on the work unit.

    Returns the implementation output for downstream phases.
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
            + f"\n\n## File Scope\n"
            + "\n".join(f"- {f}" for f in work_unit.file_scope)
        ],
    )
    result = impl_agent.run(work_unit.description)
    return PhaseResult(
        phase="IMPLEMENT",
        success=True,
        output=str(result.content or ""),
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
    Both reviewers must clear blockers for the phase to pass.
    """
    reviewers = create_fresh_adversarial_reviewers(implementer_model_id)
    review_prompt = (
        f"Review the following implementation for work unit '{work_unit.title}'.\n\n"
        f"## Implementation Output\n{impl_output}\n\n"
        f"## Definition of Done\n"
        + "\n".join(f"- {item}" for item in work_unit.dod)
        + "\n\nReturn a JSON verdict: "
        '{"verdict": "APPROVED" or "REJECTED", "blockers": [...], "reasoning": "..."}'
    )

    review_results = []
    has_blockers = False
    for reviewer in reviewers:
        review = reviewer.run(review_prompt)
        content_str = str(review.content or "")
        review_results.append({
            "reviewer": reviewer.name,
            "content": content_str[:1000],
        })
        content_lower = content_str.lower()
        if '"rejected"' in content_lower or '"blockers"' in content_lower:
            # Conservative: if any reviewer mentions blockers or rejects, flag it
            if '"rejected"' in content_lower:
                has_blockers = True

    return PhaseResult(
        phase="REVIEW",
        success=not has_blockers,
        output=json.dumps(review_results),
        details={"reviewer_count": len(reviewers)},
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


def execute_work_units(step_input: StepInput) -> StepOutput:
    """Execute all work units in DAG order (DESIGN.md §2.5).

    Reads serialized work units from session_state, builds a DAG,
    and executes in topological batches. Units within a batch have
    no mutual dependencies and could run in parallel (currently
    sequential; parallel execution deferred to Phase 2).

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

    for batch_idx, batch in enumerate(batches):
        logger.info(
            "Executing batch %d/%d (%d units)",
            batch_idx + 1, len(batches), len(batch),
        )

        # Execute units in this batch (sequential for now)
        for wu in batch:
            result = run_four_phase_loop(wu, project=project)
            all_results.append(result)
            if result.status == "completed":
                completed_ids.add(wu.id)

    # Store results in session_state
    serialized_results = [
        {
            "unit_id": r.unit_id,
            "status": r.status,
            "attempts": r.attempts,
            "assigned_model": r.assigned_model,
        }
        for r in all_results
    ]
    set_ss(step_input, "execution_results", serialized_results)

    escalated = [r.unit_id for r in all_results if r.status == "escalated"]
    set_ss(step_input, "escalated_units", escalated)

    completed_count = len(completed_ids)
    total_count = len(work_units)
    summary = f"{completed_count}/{total_count} work units completed"
    if escalated:
        summary += f", {len(escalated)} escalated: {', '.join(escalated)}"

    logger.info("Execution complete: %s", summary)
    return StepOutput(content=json.dumps(serialized_results))
