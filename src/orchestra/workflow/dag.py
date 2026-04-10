"""DAG construction and validation for WorkUnit dependency graphs.

Implements three concerns from DESIGN.md:
1. Cycle detection (AC-01 related: DAG must be valid before scheduling)
2. File-scope overlap detection (two-layer: pattern-level + file-level)
3. Topological batch scheduling (independent units parallelised per batch)
"""

from __future__ import annotations

from collections import defaultdict, deque
from fnmatch import fnmatch
from glob import glob
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestra.models.work_unit import WorkUnit


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CyclicDependencyError(Exception):
    """Raised when WorkUnit dependencies contain a cycle."""


class FileOverlapError(Exception):
    """Raised when two WorkUnits claim overlapping file scopes."""


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------

class WorkUnitDAG:
    """Directed acyclic graph over WorkUnits, indexed by ID.

    Provides topological batch iteration for the executor: each batch contains
    units whose dependencies have all been satisfied by prior batches, so units
    within a batch can run in parallel.
    """

    def __init__(self, units: list[WorkUnit]) -> None:
        self._units: dict[str, WorkUnit] = {u.id: u for u in units}
        self._adj: dict[str, list[str]] = defaultdict(list)  # id -> dependents
        self._in_degree: dict[str, int] = {u.id: 0 for u in units}

        for u in units:
            for dep_id in u.dependencies:
                self._adj[dep_id].append(u.id)
                self._in_degree[u.id] += 1

    @property
    def units(self) -> dict[str, WorkUnit]:
        return dict(self._units)

    def topological_batches(self) -> list[list[WorkUnit]]:
        """Return units grouped into batches respecting dependency order.

        Each batch is a list of WorkUnits that can execute in parallel (all
        their dependencies appear in earlier batches). Batches are returned
        in execution order.

        Raises ``CyclicDependencyError`` if the graph contains a cycle (should
        not happen if ``validate_dag`` was called first, but defensive).
        """
        in_deg = dict(self._in_degree)
        queue: deque[str] = deque(uid for uid, d in in_deg.items() if d == 0)
        batches: list[list[WorkUnit]] = []
        visited = 0

        while queue:
            batch_ids = list(queue)
            queue.clear()
            batches.append([self._units[uid] for uid in batch_ids])
            visited += len(batch_ids)

            for uid in batch_ids:
                for dep in self._adj[uid]:
                    in_deg[dep] -= 1
                    if in_deg[dep] == 0:
                        queue.append(dep)

        if visited != len(self._units):
            remaining = {uid for uid, d in in_deg.items() if d > 0}
            raise CyclicDependencyError(
                f"Cycle detected among WorkUnits: {remaining}"
            )

        return batches

    def units_after_batch(self, batch_idx: int) -> list[WorkUnit]:
        """Return all units in batches after *batch_idx*."""
        batches = self.topological_batches()
        remaining: list[WorkUnit] = []
        for b in batches[batch_idx + 1 :]:
            remaining.extend(b)
        return remaining


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_dag(work_units: list[WorkUnit]) -> WorkUnitDAG:
    """Construct a WorkUnitDAG from a list of WorkUnits.

    Validates that all dependency references point to existing unit IDs
    and that no duplicate IDs exist.
    """
    seen_ids: dict[str, int] = {}
    for i, u in enumerate(work_units):
        if u.id in seen_ids:
            raise ValueError(
                f"Duplicate WorkUnit ID '{u.id}' at positions "
                f"{seen_ids[u.id]} and {i}"
            )
        seen_ids[u.id] = i

    known_ids = set(seen_ids)
    for u in work_units:
        unknown = set(u.dependencies) - known_ids
        if unknown:
            raise ValueError(
                f"WorkUnit '{u.id}' depends on unknown IDs: {unknown}"
            )
    return WorkUnitDAG(work_units)


def validate_dag(work_units: list[WorkUnit]) -> None:
    """Validate that WorkUnit dependencies form a valid DAG (no cycles).

    Raises ``CyclicDependencyError`` if a cycle is found.
    """
    dag = build_dag(work_units)
    dag.topological_batches()  # raises CyclicDependencyError on cycle


def _patterns_overlap(a: str, b: str) -> bool:
    """Check whether two file-scope patterns could match overlapping files.

    Conservative: returns True if there *might* be a file matching both patterns.
    Uses segment-by-segment comparison to detect intersections like
    ``src/*/models.py`` vs ``src/auth/*.py`` (both match ``src/auth/models.py``).
    """
    # Case 1: direct fnmatch (one pattern is a superset of the other)
    if fnmatch(a, b) or fnmatch(b, a):
        return True

    # Case 2: PurePosixPath.match (handles ** with 1+ dirs)
    try:
        if PurePosixPath(a).match(b) or PurePosixPath(b).match(a):
            return True
    except ValueError:
        pass

    # Case 3: ** prefix detection — src/**/*.py overlaps with src/auth.py
    # because ** can match zero directories
    if "**" in a or "**" in b:
        glob_pat, concrete = (a, b) if "**" in a else (b, a)
        star_idx = glob_pat.index("**")
        prefix = glob_pat[:star_idx]
        suffix_pattern = glob_pat[star_idx + 2:].lstrip("/")
        if concrete.startswith(prefix):
            remainder = concrete[len(prefix):]
            filename = PurePosixPath(remainder).name
            suffix_file_pattern = PurePosixPath(suffix_pattern).name if suffix_pattern else "*"
            if fnmatch(filename, suffix_file_pattern):
                return True

    # Case 4: segment-by-segment intersection detection
    # e.g. src/*/models.py vs src/auth/*.py -> both can match src/auth/models.py
    return _segments_can_intersect(a.split("/"), b.split("/"))


def _segments_can_intersect(segs_a: list[str], segs_b: list[str]) -> bool:
    """Check if two segment lists could match the same path.

    Each segment is compared pairwise. A wildcard segment (containing * or ?)
    is considered compatible with any concrete segment and with other wildcards.
    ``**`` matches zero or more segments so we expand possibilities.
    """
    # Handle ** by checking if either side has it — those are already
    # covered by Cases 2-3 above. Here we handle fixed-length patterns.
    if len(segs_a) != len(segs_b):
        return False

    for sa, sb in zip(segs_a, segs_b):
        # Two segments are compatible if either could match the other
        if not (fnmatch(sa, sb) or fnmatch(sb, sa)):
            # Also check if both are wildcards that could match a common value
            if _is_glob_segment(sa) and _is_glob_segment(sb):
                continue  # both are wildcards — can match same concrete value
            return False
    return True


def _is_glob_segment(s: str) -> bool:
    """Return True if a path segment contains glob wildcards."""
    return "*" in s or "?" in s


def validate_no_overlap(work_units: list[WorkUnit]) -> None:
    """Ensure WorkUnit file scopes do not overlap.

    Two-layer check per DESIGN.md \u00a76:
    1. Pattern-level: detects exact duplicates and cross-matching patterns
       (catches conflicts even for files that don't exist yet).
    2. File-level: globs patterns against the filesystem to catch cases where
       different patterns resolve to the same file.

    Raises ``FileOverlapError`` on the first detected overlap.
    """
    # Layer 1: pattern-level deduplication (inter-unit only per DESIGN §2.4)
    all_patterns: dict[str, str] = {}  # pattern -> wu.id
    for wu in work_units:
        for pattern in wu.file_scope:
            if pattern in all_patterns and all_patterns[pattern] != wu.id:
                raise FileOverlapError(
                    f"Pattern '{pattern}' claimed by both "
                    f"{all_patterns[pattern]} and {wu.id}"
                )
            for existing_pattern, existing_wu_id in all_patterns.items():
                if existing_wu_id == wu.id:
                    continue  # skip intra-unit comparisons
                if _patterns_overlap(pattern, existing_pattern):
                    raise FileOverlapError(
                        f"Patterns overlap: '{existing_pattern}' ({existing_wu_id}) "
                        f"vs '{pattern}' ({wu.id})"
                    )
            all_patterns[pattern] = wu.id

    # Layer 2: file-level expansion (filesystem-dependent, inter-unit only)
    all_files: dict[str, str] = {}  # resolved path -> wu.id
    for wu in work_units:
        for pattern in wu.file_scope:
            for f in glob(pattern):
                if f in all_files and all_files[f] != wu.id:
                    raise FileOverlapError(
                        f"File '{f}' matched by both "
                        f"{all_files[f]} and {wu.id}"
                    )
                all_files[f] = wu.id
