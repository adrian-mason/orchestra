"""Final Integration Review — cross-unit integration check (P1-09, DESIGN.md §2.6).

Runs after all work units complete the 4-phase loop. Checks cross-unit
integration via quality gates and a fresh cross-model reviewer.

The Integration Reviewer is an ad-hoc single-shot agent (DESIGN.md §2.6),
not part of the §3.1 six-role agent taxonomy. It checks interface
consistency, shared state conflicts, and integration regressions across
the merged work unit outputs.

Gate 0 Constraints:
- AC-03: Session state via get_ss()/set_ss() only
- AC-04: Decision gate as separate post-step, not embedded
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from agno.workflow.types import StepInput, StepOutput

from orchestra.model_resolver import (
    ModelsConfig,
    create_fresh_adversarial_reviewers,
    resolve_model,
)
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import check_team_member_errors
from orchestra.workflow.gate import create_decision_gate
from orchestra.workflow.plan_review import GateVerdict, format_feedback
from orchestra.workflow.quality_gates import run_quality_gates

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb

logger = logging.getLogger(__name__)

_JSON_VERDICT_RE = re.compile(
    r"\{[^{}]*\"verdict\"\s*:\s*\"[^\"]+\"[^{}]*\}",
    re.DOTALL,
)


def _is_genuine_error(content: str) -> bool:
    """Check if content contains genuine execution errors (not domain discussion)."""
    errors = check_team_member_errors(content, raise_on_error=False)
    if not errors:
        return False
    for err in errors:
        lower = err.lower()
        if "traceback (most recent call last)" in lower:
            return True
        if "error occurred during execution" in lower:
            return True
        if "member" in lower and "failed" in lower:
            return True
    return False


def _build_integration_prompt(
    execution_results: list[dict[str, Any]],
    work_units: list[dict[str, Any]],
) -> str:
    """Build the integration review prompt from execution results and work units."""
    completed = [r for r in execution_results if r.get("status") == "completed"]
    escalated = [r for r in execution_results if r.get("status") in ("escalated", "blocked")]

    unit_map = {wu.get("id", ""): wu for wu in work_units}

    lines = [
        f"Review the integration of {len(completed)} completed work units.\n",
    ]

    if escalated:
        lines.append(
            f"⚠️ {len(escalated)} units were escalated/blocked: "
            f"{', '.join(r.get('unit_id', '?') for r in escalated)}\n"
        )

    lines.append("## Completed Work Units\n")
    for result in completed:
        uid = result.get("unit_id", "unknown")
        wu = unit_map.get(uid, {})
        lines.append(f"### {uid}: {wu.get('title', 'Untitled')}")
        lines.append(f"- Model: {result.get('assigned_model', 'unknown')}")
        lines.append(f"- Attempts: {result.get('attempts', 0)}")
        if wu.get("file_scope"):
            lines.append(f"- File scope: {', '.join(wu['file_scope'])}")
        if wu.get("dependencies"):
            lines.append(f"- Dependencies: {', '.join(wu['dependencies'])}")
        lines.append("")

    lines.append(
        "## Review Focus\n"
        "Check for:\n"
        "- Interface consistency: do module inputs/outputs match across units?\n"
        "- Shared state conflicts: do multiple units modify the same files/config?\n"
        "- Integration regressions: could merging these units break existing behavior?\n"
        "- Dependency ordering: are dependency relationships honored correctly?\n\n"
        "Return a JSON verdict:\n"
        '{"reviewer": "<your name>", "verdict": "APPROVED" or "REJECTED",\n'
        ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
    )

    return "\n".join(lines)


def _parse_integration_verdicts(content: str) -> list[dict[str, Any]]:
    """Extract structured verdict dicts from integration reviewer output."""
    verdicts: list[dict[str, Any]] = []
    for match in _JSON_VERDICT_RE.finditer(content):
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "verdict" in data:
                verdicts.append(data)
        except json.JSONDecodeError:
            continue
    return verdicts


def final_integration_review(step_input: StepInput) -> StepOutput:
    """Cross-unit integration review (DESIGN.md §2.6).

    Runs after execute_work_units() completes. Performs two checks:
    1. Integration-level quality gates (test + lint + typecheck)
    2. Cross-model integration review by fresh adversarial reviewers

    Does NOT self-pause — the subsequent integration_decision_gate step
    handles blocking via requires_confirmation=True (AC-04).

    Session state reads:
        - execution_results: List of UnitResult dicts (from P1-08)
        - work_units: List of WorkUnit dicts (from P1-07)
        - project_name: Optional project name for model resolution

    Session state writes:
        - integration_gate_passed: bool
        - integration_gate_verdicts: List of verdict dicts
        - integration_review_summary: Human-readable summary

    AC-03: All session state access via get_ss()/set_ss().
    AC-04: No self-pause. Returns result for post-step Decision Gate.
    """
    execution_results = get_ss(step_input, "execution_results", [])
    if not execution_results:
        raise ValueError(
            "No execution_results in session_state. "
            "execute_work_units() must run before final_integration_review()."
        )

    work_units = get_ss(step_input, "work_units", [])
    project = get_ss(step_input, "project_name")

    completed = [r for r in execution_results if r.get("status") == "completed"]
    escalated = [r for r in execution_results if r.get("status") in ("escalated", "blocked")]

    if not completed:
        set_ss(step_input, "integration_gate_passed", False)
        set_ss(step_input, "integration_gate_verdicts", [])
        summary = f"No completed work units — {len(escalated)} escalated"
        set_ss(step_input, "integration_review_summary", summary)
        logger.warning("Integration review skipped: %s", summary)
        return StepOutput(content=json.dumps({
            "integration_passed": False,
            "summary": summary,
        }))

    # Phase 1: Integration-level quality gates
    logger.info("Running integration quality gates")
    gate_result = run_quality_gates(working_dir=get_ss(step_input, "working_dir"))
    gates_passed = gate_result.passed

    if not gates_passed:
        logger.warning("Integration quality gates failed: %s", gate_result.summary)

    # Phase 2: Cross-model integration review
    logger.info("Running cross-model integration review")
    impl_models = {
        r.get("assigned_model") for r in completed if r.get("assigned_model")
    }
    primary_model = next(iter(impl_models)) if impl_models else resolve_model(
        role="implementer", config=ModelsConfig(), project=project,
    )

    reviewers = create_fresh_adversarial_reviewers(primary_model)
    review_prompt = _build_integration_prompt(execution_results, work_units)

    all_content: list[str] = []
    for reviewer in reviewers:
        result = reviewer.run(review_prompt)
        all_content.append(str(result.content or ""))

    combined_content = "\n\n".join(all_content)

    if _is_genuine_error(combined_content):
        logger.warning("Integration reviewer execution error detected")
        set_ss(step_input, "integration_gate_passed", False)
        set_ss(step_input, "integration_gate_verdicts", [])
        summary = "Integration reviewer execution error"
        set_ss(step_input, "integration_review_summary", summary)
        return StepOutput(content=json.dumps({
            "integration_passed": False,
            "summary": summary,
        }))

    verdicts = _parse_integration_verdicts(combined_content)
    has_rejection = any(
        v.get("verdict") in ("REJECTED", "NEEDS_REVISION", "FAIL")
        for v in verdicts
    )
    if not verdicts:
        has_rejection = True

    review_passed = not has_rejection

    # Final determination
    integration_passed = gates_passed and review_passed

    set_ss(step_input, "integration_gate_passed", integration_passed)
    set_ss(step_input, "integration_gate_verdicts", verdicts)

    parts = []
    parts.append(f"{len(completed)}/{len(execution_results)} units completed")
    parts.append(f"quality gates: {'PASSED' if gates_passed else 'FAILED'}")
    parts.append(f"integration review: {'APPROVED' if review_passed else 'REJECTED'}")
    if escalated:
        parts.append(f"{len(escalated)} escalated")
    summary = ", ".join(parts)
    set_ss(step_input, "integration_review_summary", summary)

    logger.info("Integration review complete: %s", summary)
    return StepOutput(content=json.dumps({
        "integration_passed": integration_passed,
        "gates_passed": gates_passed,
        "review_passed": review_passed,
        "verdicts": verdicts,
        "summary": summary,
    }))


def create_check_integration_result(
    events_db: SqliteDb,
) -> Callable[[StepInput], StepOutput]:
    """Factory that returns a post-step function with events_db bound.

    DecisionGate records must go to events_db (not traces_db) per
    DESIGN.md §4.5, §10.1. Same closure pattern as P1-04/P1-05.

    Args:
        events_db: The events database for DecisionGate persistence.

    Returns:
        Step function with signature (StepInput) -> StepOutput.
    """

    def check_integration_result(step_input: StepInput) -> StepOutput:
        """Post-step: check if integration review passed (AC-04).

        If passed, returns INTEGRATION_REVIEW_PASSED.
        If not passed, creates a DecisionGate for human escalation.
        """
        if get_ss(step_input, "integration_gate_passed"):
            return StepOutput(content="INTEGRATION_REVIEW_PASSED")

        verdicts_data = get_ss(step_input, "integration_gate_verdicts", [])
        gate_verdicts = []
        for v in verdicts_data:
            try:
                gate_verdicts.append(GateVerdict(**v))
            except Exception:
                continue

        workflow_run_id = getattr(
            step_input.workflow_session, "session_id", "unknown"
        )

        context: dict[str, Any] = {
            "verdicts": verdicts_data,
            "summary": get_ss(step_input, "integration_review_summary", ""),
        }
        if gate_verdicts:
            context["feedback"] = format_feedback(gate_verdicts)

        gate = create_decision_gate(
            events_db,
            workflow_run_id=workflow_run_id,
            agent_id="integration_review_gate",
            gate_type="integration",
            context=context,
        )
        set_ss(step_input, "pending_decision_gate_id", gate.id)
        logger.info("Created DecisionGate %s for integration review escalation", gate.id)

        return StepOutput(content="INTEGRATION_REVIEW_FAILED")

    return check_integration_result
