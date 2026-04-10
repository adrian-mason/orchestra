"""Tests for orchestra.learning.knowledge — P0-08.

Covers: KnowledgeEntry schema, JSONL persistence (load/append/deprecate/compact),
duplicate/conflict detection, selective injection (prime_knowledge),
and failure paths per team convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.learning.knowledge import (
    MAX_INJECTION_ENTRIES,
    MIN_INJECTABLE_CONFIDENCE,
    KnowledgeEntry,
    append_entries,
    append_entry,
    compact,
    deprecate_entry,
    find_conflict,
    find_duplicate,
    load_active_knowledge,
    load_knowledge,
    prime_knowledge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides) -> KnowledgeEntry:
    """Create a KnowledgeEntry with sensible defaults."""
    defaults = dict(
        id="k-20260410-001",
        type="pattern",
        fact="Use structured logging",
        recommendation="Replace print() with logger calls",
        confidence=0.8,
        provenance="pr-42-review",
        tags=["logging"],
        affected_files=["src/orchestra/*.py"],
        created_at="2026-04-10T00:00:00+00:00",
        updated_at="2026-04-10T00:00:00+00:00",
    )
    defaults.update(overrides)
    return KnowledgeEntry(**defaults)


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    return tmp_path / "knowledge.jsonl"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestKnowledgeEntrySchema:
    def test_valid_entry(self):
        e = _make_entry()
        assert e.id == "k-20260410-001"
        assert e.type == "pattern"
        assert e.confidence == 0.8
        assert e.deprecated is False
        assert e.usage_count == 0

    def test_confidence_clamped_high(self):
        """Confidence > 1.0 should be clamped to 1.0."""
        e = _make_entry(confidence=1.5)
        assert e.confidence == 1.0

    def test_confidence_clamped_low(self):
        """Confidence < 0.0 should be clamped to 0.0."""
        e = _make_entry(confidence=-0.3)
        assert e.confidence == 0.0

    def test_confidence_boundary_zero(self):
        e = _make_entry(confidence=0.0)
        assert e.confidence == 0.0

    def test_confidence_boundary_one(self):
        e = _make_entry(confidence=1.0)
        assert e.confidence == 1.0

    def test_empty_fact_rejected(self):
        with pytest.raises(Exception):
            _make_entry(fact="")

    def test_empty_recommendation_rejected(self):
        with pytest.raises(Exception):
            _make_entry(recommendation="")

    def test_invalid_type_rejected(self):
        with pytest.raises(Exception):
            _make_entry(type="invalid_type")

    def test_negative_usage_count_rejected(self):
        with pytest.raises(Exception):
            _make_entry(usage_count=-1)

    def test_all_valid_types(self):
        for t in [
            "pattern", "gotcha", "decision", "anti_pattern",
            "convention", "dependency", "environment",
        ]:
            e = _make_entry(type=t)
            assert e.type == t

    def test_is_injectable_active_high_confidence(self):
        e = _make_entry(confidence=0.8, deprecated=False)
        assert e.is_injectable is True

    def test_is_injectable_deprecated(self):
        e = _make_entry(confidence=0.8, deprecated=True)
        assert e.is_injectable is False

    def test_is_injectable_low_confidence(self):
        e = _make_entry(confidence=0.2, deprecated=False)
        assert e.is_injectable is False

    def test_is_injectable_at_threshold(self):
        e = _make_entry(confidence=MIN_INJECTABLE_CONFIDENCE)
        assert e.is_injectable is True

    def test_is_injectable_just_below_threshold(self):
        e = _make_entry(confidence=MIN_INJECTABLE_CONFIDENCE - 0.01)
        assert e.is_injectable is False

    def test_roundtrip_json(self):
        """Entry should survive model_dump_json → model_validate_json."""
        e = _make_entry()
        raw = e.model_dump_json()
        restored = KnowledgeEntry.model_validate_json(raw)
        assert restored == e


# ---------------------------------------------------------------------------
# Persistence — load / append
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_load_missing_file(self, kb_path: Path):
        """Loading from a nonexistent file returns empty list."""
        assert load_knowledge(kb_path) == []

    def test_load_empty_file(self, kb_path: Path):
        kb_path.write_text("")
        assert load_knowledge(kb_path) == []

    def test_load_blank_lines_skipped(self, kb_path: Path):
        e = _make_entry()
        kb_path.write_text(f"\n\n{e.model_dump_json()}\n\n")
        entries = load_knowledge(kb_path)
        assert len(entries) == 1

    def test_append_and_load_single(self, kb_path: Path):
        e = _make_entry()
        append_entry(kb_path, e)
        entries = load_knowledge(kb_path)
        assert len(entries) == 1
        assert entries[0].id == e.id

    def test_append_creates_parent_dirs(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "knowledge.jsonl"
        append_entry(deep_path, _make_entry())
        assert deep_path.exists()
        assert len(load_knowledge(deep_path)) == 1

    def test_append_entries_batch(self, kb_path: Path):
        entries = [
            _make_entry(id="k-001"),
            _make_entry(id="k-002"),
            _make_entry(id="k-003"),
        ]
        append_entries(kb_path, entries)
        loaded = load_knowledge(kb_path)
        assert len(loaded) == 3
        assert [e.id for e in loaded] == ["k-001", "k-002", "k-003"]

    def test_append_entries_empty_list(self, kb_path: Path):
        """Appending empty list should not create file."""
        append_entries(kb_path, [])
        assert not kb_path.exists()

    def test_append_is_additive(self, kb_path: Path):
        """Multiple appends accumulate, not overwrite."""
        append_entry(kb_path, _make_entry(id="k-001"))
        append_entry(kb_path, _make_entry(id="k-002"))
        assert len(load_knowledge(kb_path)) == 2

    def test_load_invalid_json_line(self, kb_path: Path):
        """Invalid JSON line raises ValueError with line number."""
        kb_path.write_text('{"not valid entry"}\n')
        with pytest.raises(ValueError, match=r":1:"):
            load_knowledge(kb_path)

    def test_load_invalid_json_line_number(self, kb_path: Path):
        """Error message includes correct line number for second line."""
        e = _make_entry()
        kb_path.write_text(f"{e.model_dump_json()}\nBROKEN\n")
        with pytest.raises(ValueError, match=r":2:"):
            load_knowledge(kb_path)

    def test_load_active_filters_deprecated(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001", deprecated=False))
        append_entry(kb_path, _make_entry(id="k-002", deprecated=True))
        active = load_active_knowledge(kb_path)
        assert len(active) == 1
        assert active[0].id == "k-001"


# ---------------------------------------------------------------------------
# Deprecation
# ---------------------------------------------------------------------------

class TestDeprecation:
    def test_deprecate_existing(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001"))
        result = deprecate_entry(kb_path, "k-001")
        assert result is not None
        assert result.id == "k-001"
        # After deprecation, the file should have 2 entries:
        # original + deprecated version
        all_entries = load_knowledge(kb_path)
        assert len(all_entries) == 2
        active = load_active_knowledge(kb_path)
        assert len(active) == 0

    def test_deprecate_nonexistent(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001"))
        result = deprecate_entry(kb_path, "k-999")
        assert result is None

    def test_deprecate_already_deprecated(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001", deprecated=True))
        result = deprecate_entry(kb_path, "k-001")
        assert result is None

    def test_deprecate_with_replacement(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001"))
        replacement = _make_entry(id="k-002", fact="Updated fact")
        result = deprecate_entry(kb_path, "k-001", replacement=replacement)
        assert result is not None
        active = load_active_knowledge(kb_path)
        assert len(active) == 1
        assert active[0].id == "k-002"
        assert active[0].fact == "Updated fact"

    def test_deprecate_targets_last_active_version(self, kb_path: Path):
        """Challenger blocker: deprecate must target last active version, not first."""
        append_entry(kb_path, _make_entry(id="k-001", fact="old"))
        append_entry(kb_path, _make_entry(id="k-001", fact="new"))
        result = deprecate_entry(kb_path, "k-001")
        assert result is not None
        assert result.fact == "new"
        active = load_active_knowledge(kb_path)
        assert len(active) == 0


# ---------------------------------------------------------------------------
# Compact
# ---------------------------------------------------------------------------

class TestCompact:
    def test_compact_removes_deprecated(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001"))
        append_entry(kb_path, _make_entry(id="k-002", deprecated=True))
        removed = compact(kb_path)
        assert removed == 1
        entries = load_knowledge(kb_path)
        assert len(entries) == 1
        assert entries[0].id == "k-001"

    def test_compact_keeps_latest_version(self, kb_path: Path):
        """When same id appears multiple times, keep only latest (last)."""
        append_entry(kb_path, _make_entry(id="k-001", fact="old"))
        append_entry(kb_path, _make_entry(id="k-001", fact="new"))
        removed = compact(kb_path)
        assert removed > 0
        entries = load_knowledge(kb_path)
        assert len(entries) == 1
        assert entries[0].fact == "new"

    def test_compact_noop_no_deprecated(self, kb_path: Path):
        append_entry(kb_path, _make_entry(id="k-001"))
        removed = compact(kb_path)
        assert removed == 0

    def test_compact_empty_file(self, kb_path: Path):
        assert compact(kb_path) == 0

    def test_compact_atomic_rewrite(self, kb_path: Path):
        """After compact, no .tmp file should remain."""
        append_entry(kb_path, _make_entry(id="k-001", deprecated=True))
        append_entry(kb_path, _make_entry(id="k-002"))
        compact(kb_path)
        tmp = kb_path.with_suffix(".jsonl.tmp")
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# Duplicate & conflict detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_finds_duplicate(self):
        existing = [_make_entry(
            id="k-001", type="pattern",
            tags=["logging"], affected_files=["src/orchestra/*.py"],
        )]
        candidate = _make_entry(
            id="k-002", type="pattern",
            tags=["logging", "extra"], affected_files=["src/orchestra/*.py"],
        )
        dup = find_duplicate(candidate, existing)
        assert dup is not None
        assert dup.id == "k-001"

    def test_no_duplicate_different_type(self):
        existing = [_make_entry(type="gotcha", tags=["logging"],
                                affected_files=["src/*.py"])]
        candidate = _make_entry(type="pattern", tags=["logging"],
                                affected_files=["src/*.py"])
        assert find_duplicate(candidate, existing) is None

    def test_no_duplicate_no_tag_overlap(self):
        existing = [_make_entry(tags=["auth"], affected_files=["src/*.py"])]
        candidate = _make_entry(tags=["logging"], affected_files=["src/*.py"])
        assert find_duplicate(candidate, existing) is None

    def test_no_duplicate_no_file_overlap(self):
        existing = [_make_entry(tags=["logging"], affected_files=["tests/*.py"])]
        candidate = _make_entry(tags=["logging"], affected_files=["src/*.py"])
        assert find_duplicate(candidate, existing) is None

    def test_skips_deprecated(self):
        existing = [_make_entry(
            tags=["logging"], affected_files=["src/*.py"], deprecated=True,
        )]
        candidate = _make_entry(tags=["logging"], affected_files=["src/*.py"])
        assert find_duplicate(candidate, existing) is None

    def test_empty_existing(self):
        assert find_duplicate(_make_entry(), []) is None

    def test_finds_duplicate_pattern_overlap(self):
        """Critic blocker: glob pattern overlap, not just exact string match."""
        existing = [_make_entry(
            id="k-001", type="pattern",
            tags=["logging"], affected_files=["src/*.py"],
        )]
        candidate = _make_entry(
            id="k-002", type="pattern",
            tags=["logging"], affected_files=["src/auth.py"],
        )
        dup = find_duplicate(candidate, existing)
        assert dup is not None
        assert dup.id == "k-001"


class TestConflictDetection:
    def test_finds_pattern_vs_antipattern(self):
        existing = [_make_entry(
            type="pattern", tags=["logging"], affected_files=["src/*.py"],
        )]
        candidate = _make_entry(
            type="anti_pattern", tags=["logging"], affected_files=["src/*.py"],
        )
        conflict = find_conflict(candidate, existing)
        assert conflict is not None

    def test_finds_antipattern_vs_pattern(self):
        existing = [_make_entry(
            type="anti_pattern", tags=["logging"],
            affected_files=["src/*.py"],
        )]
        candidate = _make_entry(
            type="pattern", tags=["logging"], affected_files=["src/*.py"],
        )
        assert find_conflict(candidate, existing) is not None

    def test_no_conflict_same_type(self):
        existing = [_make_entry(type="pattern", tags=["logging"],
                                affected_files=["src/*.py"])]
        candidate = _make_entry(type="pattern", tags=["logging"],
                                affected_files=["src/*.py"])
        assert find_conflict(candidate, existing) is None

    def test_no_conflict_unrelated_types(self):
        existing = [_make_entry(type="gotcha", tags=["logging"],
                                affected_files=["src/*.py"])]
        candidate = _make_entry(type="convention", tags=["logging"],
                                affected_files=["src/*.py"])
        assert find_conflict(candidate, existing) is None

    def test_conflict_on_file_overlap_only(self):
        """Conflict found even without tag overlap if files overlap."""
        existing = [_make_entry(
            type="pattern", tags=["auth"], affected_files=["src/*.py"],
        )]
        candidate = _make_entry(
            type="anti_pattern", tags=["logging"], affected_files=["src/*.py"],
        )
        assert find_conflict(candidate, existing) is not None

    def test_conflict_pattern_overlap(self):
        """Critic blocker: glob pattern overlap detection for conflicts."""
        existing = [_make_entry(
            type="pattern", tags=["auth"], affected_files=["src/*.py"],
        )]
        candidate = _make_entry(
            type="anti_pattern", tags=["logging"], affected_files=["src/auth.py"],
        )
        assert find_conflict(candidate, existing) is not None

    def test_skips_deprecated(self):
        existing = [_make_entry(
            type="pattern", tags=["logging"], affected_files=["src/*.py"],
            deprecated=True,
        )]
        candidate = _make_entry(
            type="anti_pattern", tags=["logging"], affected_files=["src/*.py"],
        )
        assert find_conflict(candidate, existing) is None


# ---------------------------------------------------------------------------
# Selective injection — prime_knowledge
# ---------------------------------------------------------------------------

class TestPrimeKnowledge:
    def test_basic_file_match_scoring(self, kb_path: Path):
        append_entry(kb_path, _make_entry(
            id="k-001", confidence=1.0,
            affected_files=["src/orchestra/*.py"], tags=[],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/orchestra/main.py"],
            tags=[],
        )
        assert len(results) == 1
        assert results[0].id == "k-001"

    def test_tag_match_scoring(self, kb_path: Path):
        append_entry(kb_path, _make_entry(
            id="k-001", confidence=1.0,
            affected_files=[], tags=["logging"],
        ))
        # No file match → score from tags only
        results = prime_knowledge(
            kb_path,
            affected_files=[],
            tags=["logging"],
        )
        assert len(results) == 1

    def test_excludes_deprecated(self, kb_path: Path):
        append_entry(kb_path, _make_entry(
            id="k-001", deprecated=True,
            affected_files=["src/*.py"], tags=["logging"],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["logging"],
        )
        assert len(results) == 0

    def test_excludes_low_confidence(self, kb_path: Path):
        append_entry(kb_path, _make_entry(
            id="k-001", confidence=0.1,
            affected_files=["src/*.py"], tags=["logging"],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["logging"],
        )
        assert len(results) == 0

    def test_respects_max_entries(self, kb_path: Path):
        for i in range(20):
            append_entry(kb_path, _make_entry(
                id=f"k-{i:03d}", confidence=0.9,
                affected_files=["src/*.py"], tags=["test"],
            ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["test"],
            max_entries=5,
        )
        assert len(results) == 5

    def test_default_max_entries(self, kb_path: Path):
        for i in range(15):
            append_entry(kb_path, _make_entry(
                id=f"k-{i:03d}", confidence=0.9,
                affected_files=["src/*.py"], tags=["test"],
            ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["test"],
        )
        assert len(results) == MAX_INJECTION_ENTRIES

    def test_outdated_penalty(self, kb_path: Path):
        """Entry with high outdated_reports should rank lower."""
        append_entry(kb_path, _make_entry(
            id="k-good", confidence=0.8, outdated_reports=0,
            affected_files=["src/*.py"], tags=["test"],
        ))
        append_entry(kb_path, _make_entry(
            id="k-stale", confidence=0.8, outdated_reports=10,
            affected_files=["src/*.py"], tags=["test"],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["test"],
            max_entries=1,
        )
        assert len(results) == 1
        assert results[0].id == "k-good"

    def test_higher_confidence_ranks_higher(self, kb_path: Path):
        append_entry(kb_path, _make_entry(
            id="k-low", confidence=0.4,
            affected_files=["src/*.py"], tags=["test"],
        ))
        append_entry(kb_path, _make_entry(
            id="k-high", confidence=1.0,
            affected_files=["src/*.py"], tags=["test"],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["test"],
            max_entries=1,
        )
        assert results[0].id == "k-high"

    def test_zero_score_excluded(self, kb_path: Path):
        """Entry with no file or tag match gets score 0, excluded."""
        append_entry(kb_path, _make_entry(
            id="k-001", confidence=1.0,
            affected_files=["unrelated/*.py"], tags=["unrelated"],
        ))
        results = prime_knowledge(
            kb_path,
            affected_files=["src/main.py"],
            tags=["logging"],
        )
        assert len(results) == 0

    def test_empty_knowledge_base(self, kb_path: Path):
        results = prime_knowledge(kb_path, affected_files=["src/main.py"], tags=["test"])
        assert results == []
