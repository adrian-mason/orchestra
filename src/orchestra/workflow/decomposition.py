"""Work Unit Decomposition — approved design → validated WorkUnit DAG (P1-07).

DESIGN.md §2.5: Reads approved_design and github_issues from session_state,
decomposes into WorkUnit instances, validates file scope non-overlap and
DAG acyclicity.

The Decomposer is an ad-hoc single-shot agent (DESIGN.md §2.5), not part of
the §3.1 six-role agent taxonomy. It uses Agent.run() directly rather than
Team coordination because it performs structured output generation without
requiring session persistence or multi-agent interaction.

Gate 0 Constraints:
- AC-03: Session state via get_ss()/set_ss() only
- AC-06: check_team_member_errors() on agent output
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agno.workflow.types import StepInput, StepOutput

from orchestra.model_resolver import instantiate_model
from orchestra.models.work_unit import WorkUnit
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import TeamMemberError, check_team_member_errors
from orchestra.workflow.dag import validate_dag, validate_no_overlap

logger = logging.getLogger(__name__)

# Regex to extract JSON array from fenced code blocks or raw text
_JSON_ARRAY_RE = re.compile(
    r"```(?:json)?\s*(\[.*?\])\s*```",
    re.DOTALL,
)

DECOMPOSER_INSTRUCTIONS = (
    "You are a Work Unit Decomposer. Given an approved design document and "
    "optional GitHub issues, decompose the design into independent WorkUnits.\n\n"
    "Each WorkUnit must contain:\n"
    "- id: unique identifier (e.g. 'wu-001', 'wu-002')\n"
    "- title: short descriptive title\n"
    "- description: detailed implementation instructions\n"
    "- dod: list of Definition of Done checklist items (verifiable)\n"
    "- file_scope: list of glob patterns for affected files; "
    "scopes must NOT overlap between units\n"
    "- dependencies: list of prerequisite WorkUnit IDs (forms a DAG)\n"
    "- estimated_complexity: 'S', 'M', or 'L'\n\n"
    "Return a JSON array of WorkUnit objects. Ensure:\n"
    "1. File scopes are disjoint across units\n"
    "2. Dependencies form a valid DAG (no cycles)\n"
    "3. Each unit is independently implementable\n"
)


def _extract_json_array_bracket_counting(content: str) -> str:
    """Extract the first complete JSON array from text using bracket counting.

    Finds the first ``[`` and tracks nesting depth, accounting for strings
    (where brackets are not structural). Returns the matched substring.
    """
    start = content.find("[")
    if start == -1:
        raise ValueError(
            "No JSON array found in content. Expected a JSON array "
            "of WorkUnit objects."
        )
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    raise ValueError(
        "No JSON array found in content. Expected a JSON array "
        "of WorkUnit objects."
    )


def parse_work_units(content: str) -> list[WorkUnit]:
    """Parse agent output into a list of WorkUnit instances.

    Handles JSON arrays in fenced code blocks or raw text.
    Validates each WorkUnit via Pydantic model.

    Args:
        content: Agent output text containing a JSON array of WorkUnit dicts.

    Returns:
        List of validated WorkUnit instances.

    Raises:
        ValueError: If no valid JSON array found or parsing fails.
    """
    if not content or not content.strip():
        raise ValueError("Empty content — no work units to parse")

    # Try fenced code block first
    match = _JSON_ARRAY_RE.search(content)
    if match:
        raw_json = match.group(1)
    else:
        raw_json = _extract_json_array_bracket_counting(content)

    try:
        raw_units = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in work units: {e}") from e

    if not isinstance(raw_units, list):
        raise ValueError(
            f"Expected JSON array, got {type(raw_units).__name__}"
        )

    if not raw_units:
        raise ValueError("Empty work unit list — design produced no units")

    work_units: list[WorkUnit] = []
    errors: list[str] = []
    for i, raw in enumerate(raw_units):
        try:
            work_units.append(WorkUnit(**raw))
        except Exception as e:
            errors.append(f"WorkUnit at index {i}: {e}")

    if errors:
        raise ValueError(
            f"Failed to parse {len(errors)} work unit(s):\n"
            + "\n".join(errors)
        )

    return work_units


def _is_genuine_team_error(errors: list[str]) -> bool:
    """Filter check_team_member_errors results for genuine failures.

    Same heuristic as P1-03/P1-04/P1-05. Bare mentions of 'Error' in
    decomposer output are legitimate and should not trigger AC-06 rejection.
    """
    for err_context in errors:
        lower = err_context.lower()
        if "traceback (most recent call last)" in lower:
            return True
        if "error occurred during execution" in lower:
            return True
        if "member" in lower and "failed" in lower:
            return True
    return False


def decompose_work_units(step_input: StepInput) -> StepOutput:
    """Decompose approved design into a validated WorkUnit DAG.

    DESIGN.md §2.5: Reads approved_design and github_issues from
    session_state via get_ss() (AC-03). Produces a list of WorkUnit
    dataclasses. Calls validate_no_overlap() and validate_dag()
    from P0-06.

    Session state reads:
        - approved_design: The design document approved by Design Review Gate
        - github_issues: Optional list of GitHub issue dicts

    Session state writes:
        - work_units: List of WorkUnit dicts (serialized)
        - work_unit_count: Number of work units produced

    Args:
        step_input: Agno StepInput with workflow session.

    Returns:
        StepOutput with serialized work units as content.

    Raises:
        ValueError: If approved_design is missing or empty, or if
            decomposition produces invalid work units.
        CyclicDependencyError: If work unit dependencies contain a cycle.
        FileOverlapError: If work unit file scopes overlap.
    """
    # AC-03: Read from session_state, NOT previous_step_content
    approved_design = get_ss(step_input, "approved_design", "")
    if not approved_design or not approved_design.strip():
        raise ValueError(
            "No approved_design in session_state. "
            "Design Review Gate must pass before decomposition."
        )

    github_issues: list[dict[str, Any]] = get_ss(
        step_input, "github_issues", []
    )

    # Build decomposition prompt
    decompose_input = f"## Approved Design\n\n{approved_design}"
    if github_issues:
        decompose_input += (
            f"\n\n## GitHub Issues\n\n{json.dumps(github_issues, indent=2)}"
        )

    # Run decomposer agent
    from agno.agent import Agent

    decomposer = Agent(
        name="Decomposer",
        model=instantiate_model("claude-sonnet-4-6"),
        instructions=[DECOMPOSER_INSTRUCTIONS],
    )
    result = decomposer.run(decompose_input)
    agent_content = str(result.content or "")
    if not agent_content.strip():
        raise ValueError(
            "Decomposer returned empty content — model may have failed "
            "to generate work units"
        )

    # AC-06: Check for team member errors
    errors = check_team_member_errors(agent_content, raise_on_error=False)
    if errors and _is_genuine_team_error(errors):
        raise TeamMemberError(errors)

    # Parse and validate
    work_units = parse_work_units(agent_content)
    validate_no_overlap(work_units)
    validate_dag(work_units)

    # Store in session_state (AC-03)
    serialized = [wu.model_dump() for wu in work_units]
    set_ss(step_input, "work_units", serialized)
    set_ss(step_input, "work_unit_count", len(work_units))

    logger.info("Decomposed design into %d work units", len(work_units))
    return StepOutput(content=json.dumps(serialized))
