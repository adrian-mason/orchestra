"""Tests for orchestra.models.task — P0-07 Unified Task Schema.

Covers: Task model validation, sub-models, conversion helpers,
and failure paths per team convention.
"""

from __future__ import annotations

import pytest

from orchestra.models.task import (
    Artifact,
    CodeSkeleton,
    CommitSpec,
    Convergence,
    ExecutionMeta,
    ImplementationStep,
    Inherited,
    PreAnalysisStep,
    Rationale,
    Risk,
    Task,
    TaskFile,
    TaskResult,
    TaskSource,
    TaskTestSpec,
    task_from_json,
    task_to_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> Task:
    """Create a Task with sensible defaults."""
    defaults = dict(
        id="TASK-001",
        title="Create unified task schema",
        description="Adapt CCW task-schema.json for Orchestra",
        depends_on=[],
        convergence=Convergence(criteria=["Schema validates against CCW spec"]),
    )
    defaults.update(overrides)
    return Task(**defaults)


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_minimal_valid_task(self):
        t = _make_task()
        assert t.id == "TASK-001"
        assert t.status == "pending"
        assert t.result is None

    def test_missing_id(self):
        with pytest.raises(Exception):
            Task(
                title="x", description="y",
                depends_on=[], convergence=Convergence(criteria=["z"]),
            )

    def test_missing_title(self):
        with pytest.raises(Exception):
            Task(
                id="T-1", description="y",
                depends_on=[], convergence=Convergence(criteria=["z"]),
            )

    def test_missing_description(self):
        with pytest.raises(Exception):
            Task(
                id="T-1", title="x",
                depends_on=[], convergence=Convergence(criteria=["z"]),
            )

    def test_missing_convergence(self):
        with pytest.raises(Exception):
            Task(id="T-1", title="x", description="y", depends_on=[])

    def test_empty_id_rejected(self):
        with pytest.raises(Exception):
            _make_task(id="")

    def test_empty_title_rejected(self):
        with pytest.raises(Exception):
            _make_task(title="")

    def test_empty_description_rejected(self):
        with pytest.raises(Exception):
            _make_task(description="")

    def test_convergence_empty_criteria_rejected(self):
        with pytest.raises(Exception):
            _make_task(convergence=Convergence(criteria=[]))


# ---------------------------------------------------------------------------
# Classification enums
# ---------------------------------------------------------------------------


class TestClassification:
    def test_all_task_types(self):
        for t in [
            "infrastructure", "feature", "enhancement", "fix", "bugfix",
            "refactor", "testing", "test-gen", "test-fix", "docs", "chore",
        ]:
            task = _make_task(type=t)
            assert task.type == t

    def test_invalid_type_rejected(self):
        with pytest.raises(Exception):
            _make_task(type="invalid")

    def test_all_priorities(self):
        for p in ["critical", "high", "medium", "low"]:
            assert _make_task(priority=p).priority == p

    def test_invalid_priority_rejected(self):
        with pytest.raises(Exception):
            _make_task(priority="urgent")

    def test_all_efforts(self):
        for e in ["small", "medium", "large"]:
            assert _make_task(effort=e).effort == e

    def test_all_actions(self):
        for a in [
            "Create", "Update", "Implement", "Refactor",
            "Add", "Delete", "Configure", "Test", "Fix",
        ]:
            assert _make_task(action=a).action == a


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_full_convergence(self):
        c = Convergence(
            criteria=["Tests pass", "Coverage > 80%"],
            verification="pytest --cov",
            definition_of_done="All API endpoints respond correctly",
        )
        assert len(c.criteria) == 2
        assert c.verification == "pytest --cov"

    def test_criteria_required_nonempty(self):
        with pytest.raises(Exception):
            Convergence(criteria=[])


class TestTaskFile:
    def test_minimal_file(self):
        f = TaskFile(path="src/main.py")
        assert f.path == "src/main.py"
        assert f.action is None

    def test_full_file(self):
        f = TaskFile(
            path="src/auth.py",
            action="modify",
            target="AuthMiddleware",
            changes=["Add JWT validation"],
            conflict_risk="high",
        )
        assert f.action == "modify"
        assert f.conflict_risk == "high"

    def test_empty_path_rejected(self):
        with pytest.raises(Exception):
            TaskFile(path="")

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            TaskFile(path="x.py", action="rename")

    def test_invalid_conflict_risk_rejected(self):
        with pytest.raises(Exception):
            TaskFile(path="x.py", conflict_risk="extreme")


class TestImplementationStep:
    def test_string_step_number(self):
        s = ImplementationStep(step="1", description="Setup environment")
        assert s.step == "1"

    def test_int_step_number(self):
        s = ImplementationStep(step=1, description="Setup environment")
        assert s.step == 1

    def test_full_step(self):
        s = ImplementationStep(
            step="setup",
            title="Setup",
            description="Initialize project",
            modification_points=["src/main.py:10"],
            logic_flow=["Create config", "Initialize DB"],
            depends_on=["0"],
            tdd_phase="red",
            actions=["mkdir src", "touch main.py"],
        )
        assert s.tdd_phase == "red"
        assert len(s.actions) == 2

    def test_empty_description_rejected(self):
        with pytest.raises(Exception):
            ImplementationStep(step="1", description="")


class TestRisk:
    def test_valid_risk(self):
        r = Risk(
            description="Migration may fail",
            probability="High",
            impact="Medium",
            mitigation="Run in staging first",
            fallback="Rollback migration",
        )
        assert r.probability == "High"
        assert r.fallback == "Rollback migration"

    def test_missing_required_fields(self):
        with pytest.raises(Exception):
            Risk(description="x", probability="High", impact="Low")

    def test_invalid_probability(self):
        with pytest.raises(Exception):
            Risk(description="x", probability="Extreme", impact="Low", mitigation="y")


class TestTaskResult:
    def test_success_result(self):
        r = TaskResult(
            success=True,
            files_modified=["src/main.py"],
            summary="All tests pass",
            commit_hash="abc123",
        )
        assert r.success is True
        assert r.commit_hash == "abc123"

    def test_failure_result(self):
        r = TaskResult(success=False, error="Tests failed")
        assert r.success is False
        assert r.error == "Tests failed"

    def test_convergence_verified(self):
        r = TaskResult(
            success=True,
            convergence_verified=[True, True, False],
        )
        assert r.convergence_verified == [True, True, False]


class TestTaskTestSpec:
    def test_full_spec(self):
        s = TaskTestSpec(
            commands=["pytest", "pytest --cov"],
            unit=["Test auth flow"],
            integration=["Test API endpoint"],
            coverage_target=80.0,
            manual_checks=["Verify UI renders"],
            success_metrics=["Response time < 200ms"],
        )
        assert s.coverage_target == 80.0

    def test_coverage_target_bounds(self):
        with pytest.raises(Exception):
            TaskTestSpec(coverage_target=101)
        with pytest.raises(Exception):
            TaskTestSpec(coverage_target=-1)


# ---------------------------------------------------------------------------
# Scope field (string or list)
# ---------------------------------------------------------------------------


class TestScope:
    def test_string_scope(self):
        t = _make_task(scope="src/auth")
        assert t.scope == "src/auth"

    def test_list_scope(self):
        t = _make_task(scope=["src/auth", "src/middleware"])
        assert t.scope == ["src/auth", "src/middleware"]

    def test_null_scope(self):
        t = _make_task()
        assert t.scope is None


# ---------------------------------------------------------------------------
# Status and runtime
# ---------------------------------------------------------------------------


class TestRuntime:
    def test_default_status(self):
        t = _make_task()
        assert t.status == "pending"

    def test_all_statuses(self):
        for s in [
            "pending", "in_progress", "active",
            "completed", "failed", "skipped", "blocked",
        ]:
            t = _make_task(status=s)
            assert t.status == s

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            _make_task(status="cancelled")

    def test_valid_executed_at(self):
        t = _make_task(executed_at="2026-04-10T08:00:00+00:00")
        assert t.executed_at == "2026-04-10T08:00:00+00:00"

    def test_invalid_executed_at(self):
        with pytest.raises(Exception):
            _make_task(executed_at="not-a-date")


# ---------------------------------------------------------------------------
# Full task with all sections
# ---------------------------------------------------------------------------


class TestFullTask:
    def test_full_task(self):
        t = Task(
            id="IMPL-042",
            title="Implement auth middleware",
            description="Add JWT validation to all API endpoints",
            type="feature",
            priority="high",
            effort="medium",
            action="Implement",
            scope="src/auth",
            excludes=["src/auth/legacy.py"],
            focus_paths=["src/auth/middleware.py"],
            depends_on=["IMPL-041"],
            parallel_group=2,
            convergence=Convergence(
                criteria=["JWT tokens validated", "401 on invalid token"],
                verification="pytest tests/test_auth.py -v",
                definition_of_done="All API endpoints require valid JWT",
            ),
            files=[
                TaskFile(path="src/auth/middleware.py", action="create"),
                TaskFile(path="tests/test_auth.py", action="create"),
            ],
            implementation=[
                "Setup auth module",
                ImplementationStep(
                    step=2,
                    description="Implement JWT validation",
                    modification_points=["src/auth/middleware.py:1-50"],
                    tdd_phase="red",
                ),
            ],
            test=TaskTestSpec(
                commands=["pytest tests/test_auth.py"],
                coverage_target=90.0,
            ),
            risks=[
                Risk(
                    description="Token expiry edge cases",
                    probability="Medium",
                    impact="Low",
                    mitigation="Add comprehensive expiry tests",
                ),
            ],
            rationale=Rationale(
                chosen_approach="PyJWT library",
                alternatives_considered=["python-jose", "authlib"],
                decision_factors=["Lightweight", "Well-maintained"],
            ),
            meta=ExecutionMeta(agent="@code-developer", method="agent"),
            source=TaskSource(tool="workflow-plan", issue_id="GH-42"),
            commit=CommitSpec(type="feat", scope="auth"),
            status="pending",
        )
        assert t.id == "IMPL-042"
        assert len(t.files) == 2
        assert len(t.implementation) == 2
        assert isinstance(t.implementation[0], str)
        assert isinstance(t.implementation[1], ImplementationStep)


# ---------------------------------------------------------------------------
# JSON roundtrip / conversion
# ---------------------------------------------------------------------------


class TestConversion:
    def test_roundtrip_json(self):
        t = _make_task(type="feature", priority="high")
        data = task_to_json(t)
        restored = task_from_json(data)
        assert restored.id == t.id
        assert restored.type == t.type
        assert restored.convergence.criteria == t.convergence.criteria

    def test_task_from_ccw_json(self):
        """Load a CCW-style dict with nested execution_config."""
        data = {
            "id": "IMPL-001",
            "title": "Test task",
            "description": "A test",
            "depends_on": [],
            "convergence": {"criteria": ["It works"]},
            "meta": {
                "agent": "@developer",
                "execution_config": {
                    "method": "agent",
                    "enable_resume": True,
                },
            },
        }
        t = task_from_json(data)
        assert t.meta is not None
        assert t.meta.agent == "@developer"
        assert t.meta.method == "agent"
        assert t.meta.enable_resume is True

    def test_task_to_json_excludes_defaults(self):
        t = _make_task()
        data = task_to_json(t)
        assert "result" not in data
        assert "risks" not in data
        assert "files" not in data

    def test_model_dump_json_roundtrip(self):
        """Pydantic native JSON serialization roundtrip."""
        t = _make_task(type="fix", status="completed")
        raw = t.model_dump_json()
        restored = Task.model_validate_json(raw)
        assert restored == t

    def test_mixed_implementation_items(self):
        """Implementation list can contain both strings and step objects."""
        data = {
            "id": "T-1",
            "title": "Mixed impl",
            "description": "Test",
            "depends_on": [],
            "convergence": {"criteria": ["done"]},
            "implementation": [
                "Simple string step",
                {"step": 2, "description": "Detailed step"},
            ],
        }
        t = task_from_json(data)
        assert len(t.implementation) == 2
        assert isinstance(t.implementation[0], str)
        assert isinstance(t.implementation[1], ImplementationStep)

    def test_dict_commands_form(self):
        """CCW allows test.commands as object (named commands)."""
        data = {
            "id": "T-1",
            "title": "Dict cmds",
            "description": "Test",
            "depends_on": [],
            "convergence": {"criteria": ["done"]},
            "test": {
                "commands": {"run_tests": "pytest", "lint": "ruff check ."},
            },
        }
        t = task_from_json(data)
        assert t.test is not None
        assert isinstance(t.test.commands, dict)
        assert t.test.commands["run_tests"] == "pytest"

    def test_extra_fields_preserved(self):
        """Task with extra='allow' preserves unknown CCW fields."""
        t = _make_task()
        data = t.model_dump()
        data["custom_field"] = "custom_value"
        restored = Task.model_validate(data)
        assert restored.custom_field == "custom_value"  # type: ignore[attr-defined]

    def test_extended_context_roundtrip(self):
        """Extended context fields (pre_analysis, artifacts, etc.) round-trip."""
        data = {
            "id": "T-1",
            "title": "Extended ctx",
            "description": "Test",
            "depends_on": [],
            "convergence": {"criteria": ["done"]},
            "pre_analysis": [
                {"step": "check_deps", "action": "List dependencies"},
            ],
            "artifacts": [
                {"type": "brainstorm", "source": "session-1", "path": "/tmp/a.md"},
            ],
            "inherited": {"from": "TASK-000", "context": ["parent setup"]},
            "code_skeleton": {
                "interfaces": [{"name": "IFoo", "methods": ["bar"]}],
                "key_functions": [],
                "classes": [],
            },
            "context_package_path": ".task-context/T-1.md",
            "evidence": ["test output log", {"type": "coverage", "value": 95}],
        }
        t = task_from_json(data)
        assert len(t.pre_analysis) == 1
        assert t.pre_analysis[0].step == "check_deps"
        assert len(t.artifacts) == 1
        assert t.artifacts[0].source == "session-1"
        assert t.inherited is not None
        assert t.inherited.from_task == "TASK-000"
        assert t.inherited.context == ["parent setup"]
        assert t.code_skeleton is not None
        assert len(t.code_skeleton.interfaces) == 1
        assert t.context_package_path == ".task-context/T-1.md"
        assert len(t.evidence) == 2

        # Round-trip
        dumped = task_to_json(t)
        restored = task_from_json(dumped)
        assert restored.pre_analysis[0].step == "check_deps"
        assert restored.context_package_path == ".task-context/T-1.md"

    def test_execution_config_roundtrip(self):
        """task_to_json rebuilds nested execution_config for CCW compat."""
        data = {
            "id": "T-1",
            "title": "EC roundtrip",
            "description": "Test",
            "depends_on": [],
            "convergence": {"criteria": ["done"]},
            "meta": {
                "agent": "@dev",
                "execution_config": {
                    "method": "agent",
                    "cli_tool": "codex",
                    "enable_resume": True,
                },
            },
        }
        t = task_from_json(data)
        assert t.meta is not None
        assert t.meta.method == "agent"
        assert t.meta.cli_tool == "codex"

        # Serialize back — must rebuild nested execution_config
        out = task_to_json(t)
        assert "execution_config" in out["meta"]
        ec = out["meta"]["execution_config"]
        assert ec["method"] == "agent"
        assert ec["cli_tool"] == "codex"
        assert ec["enable_resume"] is True
        # method/cli_tool/enable_resume should NOT be at meta top-level
        assert "method" not in out["meta"]
        assert "cli_tool" not in out["meta"]

    def test_inherited_alias_roundtrip(self):
        """Inherited.from_task serializes as 'from' via by_alias."""
        data = {
            "id": "T-1",
            "title": "Alias test",
            "description": "Test",
            "depends_on": [],
            "convergence": {"criteria": ["done"]},
            "inherited": {"from": "PARENT-001", "context": ["setup done"]},
        }
        t = task_from_json(data)
        assert t.inherited is not None
        assert t.inherited.from_task == "PARENT-001"

        out = task_to_json(t)
        # Must use alias "from", not field name "from_task"
        assert "from" in out["inherited"]
        assert "from_task" not in out["inherited"]

        # Full roundtrip
        restored = task_from_json(out)
        assert restored.inherited is not None
        assert restored.inherited.from_task == "PARENT-001"
