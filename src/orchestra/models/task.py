"""Unified Task Schema — Pydantic adaptation of CCW task-schema.json.

P0-07: Adapts the CCW unified task schema for Orchestra's Python stack.
Covers IDENTITY, CLASSIFICATION, SCOPE, DEPENDENCIES, CONVERGENCE, FILES,
IMPLEMENTATION, TEST, EXECUTION, and RUNTIME sections.

The schema is designed to be compatible with both:
- CCW task-schema.json (external tooling, JSON serialization)
- Orchestra WorkUnit (internal decomposition, §2.5)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums / Literals
# ---------------------------------------------------------------------------

TaskType = Literal[
    "infrastructure", "feature", "enhancement", "fix", "bugfix",
    "refactor", "testing", "test-gen", "test-fix", "docs", "chore",
]

TaskPriority = Literal["critical", "high", "medium", "low"]

TaskEffort = Literal["small", "medium", "large"]

TaskAction = Literal[
    "Create", "Update", "Implement", "Refactor",
    "Add", "Delete", "Configure", "Test", "Fix",
]

FileAction = Literal["modify", "create", "delete"]

ConflictRisk = Literal["low", "medium", "high"]

TaskStatus = Literal[
    "pending", "in_progress", "active",
    "completed", "failed", "skipped", "blocked",
]

CommitType = Literal["feat", "fix", "refactor", "test", "docs", "chore"]

RiskProbability = Literal["Low", "Medium", "High"]

ExecutionMethod = Literal["agent", "cli"]

ExecutionStrategy = Literal["new", "resume", "fork", "merge_fork"]

OnErrorStrategy = Literal["fail", "skip_optional", "continue"]

TddPhase = Literal["red", "green", "refactor"]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class Convergence(BaseModel):
    """Testable completion criteria — required for every task."""

    criteria: list[str] = Field(min_length=1, description="Testable completion conditions")
    verification: str | None = Field(
        default=None, description="Executable verification step"
    )
    definition_of_done: str | None = Field(
        default=None, description="Business-language completion definition"
    )


class TaskFile(BaseModel):
    """File-level modification point."""

    path: str = Field(min_length=1, description="File path")
    action: FileAction | None = Field(default=None, description="File operation type")
    target: str | None = Field(
        default=None, description="Modification target (function/class name)"
    )
    changes: list[str] = Field(
        default_factory=list, description="Change descriptions"
    )
    change: str | None = Field(
        default=None, description="Single change description"
    )
    conflict_risk: ConflictRisk | None = Field(
        default=None, description="Conflict risk level"
    )


class ImplementationStep(BaseModel):
    """Detailed implementation step."""

    step: str | int = Field(description="Step number or name")
    title: str | None = Field(default=None, description="Step title")
    description: str = Field(min_length=1, description="Step description")
    modification_points: list[str] = Field(
        default_factory=list, description="Quantified modification points"
    )
    logic_flow: list[str] = Field(
        default_factory=list, description="Implementation logic sequence"
    )
    depends_on: list[str | int] = Field(
        default_factory=list, description="Dependent steps"
    )
    output: str | None = Field(default=None, description="Output variable name")
    tdd_phase: TddPhase | None = Field(default=None, description="TDD phase")
    actions: list[str] = Field(default_factory=list, description="Specific actions")
    test_fix_cycle: dict[str, Any] | None = Field(
        default=None, description="Test fix cycle config (max_iterations etc.)"
    )


class TaskTestSpec(BaseModel):
    """Test requirements."""

    commands: list[str] | dict[str, str] = Field(
        default_factory=list,
        description="Test commands (array or named object like {run_tests: 'pytest'})",
    )
    unit: list[str] = Field(
        default_factory=list, description="Unit test requirements"
    )
    integration: list[str] = Field(
        default_factory=list, description="Integration test requirements"
    )
    coverage_target: float | None = Field(
        default=None, ge=0, le=100, description="Coverage target (%)"
    )
    manual_checks: list[str] = Field(
        default_factory=list, description="Manual verification steps"
    )
    success_metrics: list[str] = Field(
        default_factory=list, description="Quantified success metrics"
    )
    reusable_tools: list[str] = Field(
        default_factory=list, description="Reusable test tools/scripts"
    )


class Risk(BaseModel):
    """Structured risk assessment."""

    description: str = Field(min_length=1)
    probability: RiskProbability
    impact: RiskProbability
    mitigation: str = Field(min_length=1)
    fallback: str | None = Field(default=None, description="Fallback if mitigation fails")


class Rationale(BaseModel):
    """Design decision rationale."""

    chosen_approach: str | None = Field(default=None)
    alternatives_considered: list[str] = Field(default_factory=list)
    decision_factors: list[str] = Field(default_factory=list)
    tradeoffs: str | None = Field(default=None)


class Reference(BaseModel):
    """Reference implementation pointers."""

    pattern: str | None = Field(default=None, description="Reference pattern name")
    files: list[str] = Field(default_factory=list, description="Reference file paths")
    examples: str | None = Field(default=None, description="Reference guide or examples")


class ExecutionMeta(BaseModel):
    """Execution metadata."""

    agent: str | None = Field(default=None, description="Assigned agent")
    module: str | None = Field(default=None, description="Module (frontend/backend/shared)")
    method: ExecutionMethod | None = Field(default=None, description="Execution method")
    cli_tool: str | None = Field(default=None, description="CLI tool selection")
    enable_resume: bool = Field(default=False, description="Enable session resume")


class CliExecution(BaseModel):
    """CLI execution configuration."""

    id: str | None = Field(default=None, description="CLI session ID")
    strategy: ExecutionStrategy = Field(default="new", description="CLI execution strategy")
    resume_from: str | None = Field(default=None, description="Parent task CLI ID")
    merge_from: list[str] = Field(
        default_factory=list, description="Merge source CLI IDs"
    )


class TaskSource(BaseModel):
    """Task provenance."""

    tool: str | None = Field(default=None, description="Producing tool name")
    session_id: str | None = Field(default=None, description="Source session ID")
    original_id: str | None = Field(default=None, description="Pre-conversion ID")
    issue_id: str | None = Field(default=None, description="Related issue ID")


class CommitSpec(BaseModel):
    """Commit message template."""

    type: CommitType | None = Field(default=None)
    scope: str | None = Field(default=None)
    message_template: str | None = Field(default=None)


class PreAnalysisStep(BaseModel):
    """Pre-execution analysis step."""

    step: str = Field(min_length=1, description="Step name")
    action: str = Field(min_length=1, description="Action description")
    commands: list[str] = Field(default_factory=list, description="Commands to execute")
    command: str | None = Field(default=None, description="Single command")
    output_to: str | None = Field(default=None, description="Output storage location")
    on_error: OnErrorStrategy | None = Field(default=None, description="Error handling")


class Artifact(BaseModel):
    """Brainstorming artifact reference."""

    model_config = ConfigDict(extra="allow")

    type: str | None = Field(default=None, description="Artifact type")
    source: str | None = Field(default=None, description="Artifact source")
    path: str | None = Field(default=None, description="Artifact path")
    feature_id: str | None = Field(default=None, description="Related feature ID")
    priority: str | None = Field(default=None, description="Priority")
    usage: str | None = Field(default=None, description="Usage instructions")


class Inherited(BaseModel):
    """Context inherited from parent task."""

    model_config = ConfigDict(populate_by_name=True)

    from_task: str | None = Field(default=None, alias="from", description="Parent task ID")
    context: list[str] = Field(default_factory=list, description="Inherited context entries")


class CodeSkeleton(BaseModel):
    """Code skeleton for high-complexity tasks."""

    interfaces: list[dict[str, Any]] = Field(
        default_factory=list, description="Key interface/type definitions"
    )
    key_functions: list[dict[str, Any]] = Field(
        default_factory=list, description="Key function signatures"
    )
    classes: list[dict[str, Any]] = Field(
        default_factory=list, description="Key class structures"
    )


class TaskResult(BaseModel):
    """Execution result — filled by runtime."""

    success: bool
    files_modified: list[str] = Field(default_factory=list)
    summary: str | None = Field(default=None)
    error: str | None = Field(default=None)
    convergence_verified: list[bool] = Field(default_factory=list)
    commit_hash: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Main Task model
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """Unified Task — Pydantic adaptation of CCW task-schema.json v1.0.

    Required fields: id, title, description, depends_on, convergence.
    All other sections are optional, populated by different producers
    (workflow-plan, lite-plan, issue-resolve, etc.).
    """

    model_config = ConfigDict(extra="allow")

    # --- IDENTITY (required) ---
    id: str = Field(min_length=1, description="Task ID (e.g. TASK-001, IMPL-001)")
    title: str = Field(min_length=1, description="Task title (verb + target)")
    description: str = Field(min_length=1, description="Goal + reason (1-3 sentences)")

    # --- CLASSIFICATION (optional) ---
    type: TaskType | None = Field(default=None, description="Task type")
    priority: TaskPriority | None = Field(default=None, description="Priority")
    effort: TaskEffort | None = Field(default=None, description="Effort estimate")
    action: TaskAction | None = Field(default=None, description="Action verb")

    # --- SCOPE (optional) ---
    scope: str | list[str] | None = Field(
        default=None, description="Coverage scope (module path or area)"
    )
    excludes: list[str] = Field(
        default_factory=list, description="Explicitly excluded scope"
    )
    focus_paths: list[str] = Field(
        default_factory=list, description="Focus file/directory paths"
    )

    # --- DEPENDENCIES (required) ---
    depends_on: list[str] = Field(
        default_factory=list, description="Dependency task IDs (empty = no deps)"
    )
    parallel_group: int | None = Field(
        default=None, description="Parallel group number"
    )

    # --- CONVERGENCE (required) ---
    convergence: Convergence = Field(description="Testable completion criteria")

    # --- FILES (optional) ---
    files: list[TaskFile] = Field(
        default_factory=list, description="File-level modification points"
    )

    # --- IMPLEMENTATION (optional) ---
    implementation: list[str | ImplementationStep] = Field(
        default_factory=list, description="Implementation steps (string or detailed)"
    )

    # --- TEST (optional) ---
    test: TaskTestSpec | None = Field(default=None, description="Test requirements")

    # --- REGRESSION (optional) ---
    regression: list[str] = Field(
        default_factory=list, description="Regression check points"
    )

    # --- PLANNING (optional) ---
    reference: Reference | None = Field(default=None)
    rationale: Rationale | None = Field(default=None)
    risks: list[Risk] = Field(default_factory=list)

    # --- EXECUTION (optional) ---
    meta: ExecutionMeta | None = Field(default=None)
    cli_execution: CliExecution | None = Field(default=None)

    # --- CONTEXT (optional) ---
    source: TaskSource | None = Field(default=None)
    inputs: list[str] = Field(default_factory=list, description="Consumed artifacts")
    outputs: list[str] = Field(default_factory=list, description="Produced artifacts")
    commit: CommitSpec | None = Field(default=None)

    # --- EXTENDED CONTEXT (optional, CCW extended_context section) ---
    pre_analysis: list[PreAnalysisStep] = Field(
        default_factory=list, description="Pre-execution analysis steps"
    )
    artifacts: list[Artifact] = Field(
        default_factory=list, description="Brainstorming artifact references"
    )
    inherited: Inherited | None = Field(
        default=None, description="Context inherited from parent task"
    )
    code_skeleton: CodeSkeleton | None = Field(
        default=None, description="Code skeleton for high-complexity tasks"
    )
    context_package_path: str | None = Field(
        default=None, description="Path to context package file"
    )
    evidence: list[Any] = Field(
        default_factory=list, description="Supporting evidence entries"
    )

    # --- RUNTIME (filled by execution engine) ---
    status: TaskStatus = Field(default="pending", description="Execution status")
    executed_at: str | None = Field(default=None, description="ISO 8601 timestamp")
    result: TaskResult | None = Field(default=None, description="Execution result")

    @field_validator("executed_at")
    @classmethod
    def _validate_executed_at(cls, v: str | None) -> str | None:
        if v is not None:
            datetime.fromisoformat(v)
        return v


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def task_from_json(data: dict[str, Any]) -> Task:
    """Create a Task from a CCW task-schema.json dict.

    Handles the CCW convention where execution_config is nested
    under meta, and implementation items can be strings or objects.
    """
    # Flatten meta.execution_config into ExecutionMeta fields
    if "meta" in data and isinstance(data["meta"], dict):
        meta = data["meta"]
        ec = meta.pop("execution_config", None)
        if isinstance(ec, dict):
            meta.setdefault("method", ec.get("method"))
            meta.setdefault("cli_tool", ec.get("cli_tool"))
            meta.setdefault("enable_resume", ec.get("enable_resume", False))

    return Task.model_validate(data)


def task_to_json(task: Task) -> dict[str, Any]:
    """Serialize a Task to a CCW-compatible dict, excluding None/empty values.

    Rebuilds the CCW nested ``meta.execution_config`` structure and uses
    ``by_alias=True`` so aliased fields (e.g. Inherited.from) serialize
    with their JSON-facing names.
    """
    data = task.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)

    # Rebuild CCW nested execution_config inside meta
    if "meta" in data and isinstance(data["meta"], dict):
        meta = data["meta"]
        ec_keys = ("method", "cli_tool", "enable_resume")
        ec = {k: meta.pop(k) for k in ec_keys if k in meta}
        if ec:
            meta["execution_config"] = ec

    return data
