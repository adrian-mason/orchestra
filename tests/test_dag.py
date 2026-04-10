"""Tests for WorkUnit DAG construction, validation, and topological scheduling.

Covers both happy-path and failure-path scenarios per Challenger's requirement
that all submissions include failure-path evidence.
"""

import pytest

from orchestra.models.work_unit import WorkUnit
from orchestra.workflow.dag import (
    CyclicDependencyError,
    FileOverlapError,
    WorkUnitDAG,
    build_dag,
    validate_dag,
    validate_no_overlap,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _wu(id: str, deps: list[str] | None = None, files: list[str] | None = None) -> WorkUnit:
    """Shorthand WorkUnit factory for tests."""
    return WorkUnit(
        id=id,
        title=f"Unit {id}",
        description=f"Description for {id}",
        dod=[f"DoD for {id}"],
        file_scope=files or [f"src/{id}.py"],
        dependencies=deps or [],
        estimated_complexity="M",
    )


# ---------------------------------------------------------------------------
# build_dag
# ---------------------------------------------------------------------------

class TestBuildDag:
    def test_empty_list(self):
        dag = build_dag([])
        assert dag.units == {}

    def test_single_unit(self):
        dag = build_dag([_wu("a")])
        assert "a" in dag.units

    def test_valid_dependencies(self):
        dag = build_dag([_wu("a"), _wu("b", deps=["a"])])
        assert "b" in dag.units

    def test_unknown_dependency_raises(self):
        with pytest.raises(ValueError, match="unknown IDs"):
            build_dag([_wu("a", deps=["nonexistent"])])

    def test_multiple_unknown_deps(self):
        with pytest.raises(ValueError, match="unknown IDs"):
            build_dag([_wu("a", deps=["x", "y"])])

    def test_duplicate_id_raises(self):
        with pytest.raises(ValueError, match="Duplicate WorkUnit ID 'a'"):
            build_dag([_wu("a"), _wu("a")])

    def test_duplicate_id_positions(self):
        with pytest.raises(ValueError, match="positions 0 and 1"):
            build_dag([_wu("a"), _wu("a")])

    def test_duplicate_id_among_many(self):
        with pytest.raises(ValueError, match="Duplicate WorkUnit ID 'b'"):
            build_dag([_wu("a"), _wu("b"), _wu("c"), _wu("b")])


# ---------------------------------------------------------------------------
# validate_dag — cycle detection
# ---------------------------------------------------------------------------

class TestValidateDag:
    def test_linear_chain(self):
        units = [_wu("a"), _wu("b", ["a"]), _wu("c", ["b"])]
        validate_dag(units)  # should not raise

    def test_diamond(self):
        units = [
            _wu("a"),
            _wu("b", ["a"]),
            _wu("c", ["a"]),
            _wu("d", ["b", "c"]),
        ]
        validate_dag(units)  # should not raise

    def test_self_cycle(self):
        with pytest.raises(CyclicDependencyError, match="Cycle"):
            validate_dag([_wu("a", deps=["a"])])

    def test_two_node_cycle(self):
        with pytest.raises(CyclicDependencyError, match="Cycle"):
            validate_dag([_wu("a", deps=["b"]), _wu("b", deps=["a"])])

    def test_three_node_cycle(self):
        with pytest.raises(CyclicDependencyError, match="Cycle"):
            validate_dag([
                _wu("a", deps=["c"]),
                _wu("b", deps=["a"]),
                _wu("c", deps=["b"]),
            ])

    def test_cycle_with_valid_prefix(self):
        """Cycle in subset while other nodes are valid."""
        with pytest.raises(CyclicDependencyError):
            validate_dag([
                _wu("a"),
                _wu("b", deps=["a"]),
                _wu("c", deps=["d"]),
                _wu("d", deps=["c"]),
            ])

    def test_independent_units(self):
        validate_dag([_wu("a"), _wu("b"), _wu("c")])  # no deps, no cycle


# ---------------------------------------------------------------------------
# topological_batches
# ---------------------------------------------------------------------------

class TestTopologicalBatches:
    def test_all_independent(self):
        dag = build_dag([_wu("a"), _wu("b"), _wu("c")])
        batches = dag.topological_batches()
        assert len(batches) == 1
        ids = {u.id for u in batches[0]}
        assert ids == {"a", "b", "c"}

    def test_linear_chain(self):
        dag = build_dag([_wu("a"), _wu("b", ["a"]), _wu("c", ["b"])])
        batches = dag.topological_batches()
        assert len(batches) == 3
        assert batches[0][0].id == "a"
        assert batches[1][0].id == "b"
        assert batches[2][0].id == "c"

    def test_diamond(self):
        dag = build_dag([
            _wu("a"),
            _wu("b", ["a"]),
            _wu("c", ["a"]),
            _wu("d", ["b", "c"]),
        ])
        batches = dag.topological_batches()
        assert len(batches) == 3
        assert batches[0][0].id == "a"
        mid_ids = {u.id for u in batches[1]}
        assert mid_ids == {"b", "c"}
        assert batches[2][0].id == "d"

    def test_wide_then_narrow(self):
        """Many independent units followed by one that depends on all."""
        roots = [_wu(f"r{i}") for i in range(5)]
        sink = _wu("sink", deps=[f"r{i}" for i in range(5)])
        dag = build_dag(roots + [sink])
        batches = dag.topological_batches()
        assert len(batches) == 2
        assert len(batches[0]) == 5
        assert batches[1][0].id == "sink"


# ---------------------------------------------------------------------------
# units_after_batch
# ---------------------------------------------------------------------------

class TestUnitsAfterBatch:
    def test_returns_remaining(self):
        dag = build_dag([_wu("a"), _wu("b", ["a"]), _wu("c", ["b"])])
        remaining = dag.units_after_batch(0)
        ids = {u.id for u in remaining}
        assert ids == {"b", "c"}

    def test_last_batch_returns_empty(self):
        dag = build_dag([_wu("a"), _wu("b", ["a"])])
        remaining = dag.units_after_batch(1)
        assert remaining == []


# ---------------------------------------------------------------------------
# validate_no_overlap — file scope
# ---------------------------------------------------------------------------

class TestValidateNoOverlap:
    def test_disjoint_patterns(self):
        units = [
            _wu("a", files=["src/auth/*.py"]),
            _wu("b", files=["src/db/*.py"]),
        ]
        validate_no_overlap(units)  # should not raise

    def test_exact_duplicate_pattern(self):
        units = [
            _wu("a", files=["src/auth/*.py"]),
            _wu("b", files=["src/auth/*.py"]),
        ]
        with pytest.raises(FileOverlapError, match="claimed by both"):
            validate_no_overlap(units)

    def test_cross_matching_patterns(self):
        units = [
            _wu("a", files=["src/*.py"]),
            _wu("b", files=["src/auth.py"]),
        ]
        with pytest.raises(FileOverlapError, match="Patterns overlap"):
            validate_no_overlap(units)

    def test_recursive_glob_overlap(self):
        """** recursive glob must detect overlap with concrete paths."""
        units = [
            _wu("a", files=["src/**/*.py"]),
            _wu("b", files=["src/auth.py"]),
        ]
        with pytest.raises(FileOverlapError, match="Patterns overlap"):
            validate_no_overlap(units)

    def test_recursive_glob_vs_subdir(self):
        units = [
            _wu("a", files=["src/**/*.py"]),
            _wu("b", files=["src/deep/nested/file.py"]),
        ]
        with pytest.raises(FileOverlapError, match="Patterns overlap"):
            validate_no_overlap(units)

    def test_no_file_scopes(self):
        units = [_wu("a"), _wu("b")]
        validate_no_overlap(units)  # should not raise

    def test_empty_list(self):
        validate_no_overlap([])  # should not raise

    def test_single_unit_no_conflict(self):
        validate_no_overlap([_wu("a", files=["src/**/*.py"])])

    def test_multiple_patterns_per_unit_no_conflict(self):
        units = [
            _wu("a", files=["src/auth/*.py", "tests/test_auth.py"]),
            _wu("b", files=["src/db/*.py", "tests/test_db.py"]),
        ]
        validate_no_overlap(units)

    def test_intra_unit_overlap_allowed(self):
        """Patterns within the same unit are allowed to overlap (DESIGN §2.4)."""
        units = [
            _wu("a", files=["src/*.py", "src/auth.py"]),
        ]
        validate_no_overlap(units)  # should not raise

    def test_pattern_intersection_detected(self):
        """Two patterns that can match the same file via different wildcards."""
        units = [
            _wu("a", files=["src/*/models.py"]),
            _wu("b", files=["src/auth/*.py"]),
        ]
        with pytest.raises(FileOverlapError, match="Patterns overlap"):
            validate_no_overlap(units)
