"""Knowledge base schema and JSONL persistence.

Implements DESIGN.md §9.2 KnowledgeEntry schema and §9.3 selective injection.
Append-only write semantics: updates append a new version and mark the old as deprecated.

P0-08: Knowledge JSONL Schema + Pydantic model
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

ENTRY_TYPES = Literal[
    "pattern",
    "gotcha",
    "decision",
    "anti_pattern",
    "convention",
    "dependency",
    "environment",
]

MIN_INJECTABLE_CONFIDENCE = 0.3
MAX_INJECTION_ENTRIES = 10


class KnowledgeEntry(BaseModel):
    """A single knowledge entry in the knowledge base.

    Matches DESIGN.md §9.2 schema. Append-only: entries are never modified
    in-place. To update, append a new version with the same id and mark
    the old one deprecated.
    """

    id: str = Field(description="Unique identifier, format: k-{date}-{seq}")
    type: ENTRY_TYPES = Field(description="Category of knowledge")
    fact: str = Field(min_length=1, description="Factual description")
    recommendation: str = Field(min_length=1, description="Suggested action")
    confidence: float = Field(description="Confidence score 0.0-1.0, clamped by validator")
    provenance: str = Field(description="Source reference, e.g. pr-123-review")
    tags: list[str] = Field(default_factory=list, description="Topic tags")
    affected_files: list[str] = Field(
        default_factory=list, description="File glob patterns"
    )
    usage_count: int = Field(default=0, ge=0, description="Times injected")
    helpful_count: int = Field(default=0, ge=0, description="Times adopted")
    outdated_reports: int = Field(default=0, ge=0, description="Times marked outdated")
    deprecated: bool = Field(default=False, description="Superseded by newer version")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 creation timestamp",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 last-update timestamp",
    )

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @property
    def is_injectable(self) -> bool:
        return not self.deprecated and self.confidence >= MIN_INJECTABLE_CONFIDENCE


# ---------------------------------------------------------------------------
# Persistence — append-only JSONL
# ---------------------------------------------------------------------------


def load_knowledge(path: str | Path) -> list[KnowledgeEntry]:
    """Load all entries (including deprecated) from a knowledge.jsonl file."""
    path = Path(path)
    if not path.exists():
        return []
    entries: list[KnowledgeEntry] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(KnowledgeEntry.model_validate_json(stripped))
            except Exception as exc:
                raise ValueError(
                    f"{path}:{line_no}: invalid KnowledgeEntry: {exc}"
                ) from exc
    return entries


def load_active_knowledge(path: str | Path) -> list[KnowledgeEntry]:
    """Load only non-deprecated entries, deduplicating by id (last version wins).

    This correctly handles append-only deprecation where the original line
    remains in the file but a newer deprecated version was appended.
    """
    entries = load_knowledge(path)
    # Last occurrence of each id wins (append-only semantics)
    by_id: dict[str, KnowledgeEntry] = {}
    for e in entries:
        by_id[e.id] = e
    return [e for e in by_id.values() if not e.deprecated]


def append_entry(path: str | Path, entry: KnowledgeEntry) -> None:
    """Append a single entry to the knowledge JSONL file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")


def append_entries(path: str | Path, entries: list[KnowledgeEntry]) -> None:
    """Append multiple entries atomically (single open)."""
    if not entries:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry.model_dump_json() + "\n")


def deprecate_entry(
    path: str | Path, entry_id: str, replacement: KnowledgeEntry | None = None
) -> KnowledgeEntry | None:
    """Mark an entry as deprecated by appending an updated version.

    If `replacement` is provided, it is also appended as the new version.
    Returns the deprecated entry, or None if not found.
    """
    entries = load_knowledge(path)
    target = None
    for e in entries:
        if e.id == entry_id and not e.deprecated:
            target = e  # last active version wins (append-only semantics)
    if target is None:
        return None

    deprecated_version = target.model_copy(
        update={
            "deprecated": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    to_append = [deprecated_version]
    if replacement is not None:
        to_append.append(replacement)
    append_entries(path, to_append)
    return target


def compact(path: str | Path) -> int:
    """Remove deprecated entries, keeping only the latest version of each id.

    Returns the number of entries removed.
    """
    entries = load_knowledge(path)
    if not entries:
        return 0

    # Keep only the latest version of each id (last occurrence wins)
    seen: dict[str, KnowledgeEntry] = {}
    for entry in entries:
        seen[entry.id] = entry

    # Filter out deprecated entries
    active = [e for e in seen.values() if not e.deprecated]
    removed = len(entries) - len(active)

    if removed == 0:
        return 0

    # Rewrite file atomically
    path = Path(path)
    tmp_path = path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for entry in active:
            f.write(entry.model_dump_json() + "\n")
    os.replace(tmp_path, path)
    return removed


# ---------------------------------------------------------------------------
# Duplicate detection & conflict resolution
# ---------------------------------------------------------------------------


def _patterns_overlap(patterns_a: list[str], patterns_b: list[str]) -> bool:
    """Check if two sets of file glob patterns could match overlapping files.

    Two pattern sets overlap if any pattern from one set matches any pattern
    from the other (treating one as a filename and the other as a glob), or
    if they are identical strings.
    """
    for a in patterns_a:
        for b in patterns_b:
            if a == b or fnmatch(a, b) or fnmatch(b, a):
                return True
    return False


def find_duplicate(
    candidate: KnowledgeEntry, existing: list[KnowledgeEntry]
) -> KnowledgeEntry | None:
    """Find an existing active entry that covers the same knowledge.

    Match criteria: same type + overlapping tags + overlapping affected_files
    (using pattern-based matching, not exact string equality).
    """
    if not existing:
        return None
    candidate_tags = set(candidate.tags)
    for entry in existing:
        if entry.deprecated:
            continue
        if entry.type != candidate.type:
            continue
        tag_overlap = candidate_tags & set(entry.tags)
        file_overlap = _patterns_overlap(candidate.affected_files, entry.affected_files)
        if tag_overlap and file_overlap:
            return entry
    return None


def find_conflict(
    candidate: KnowledgeEntry, existing: list[KnowledgeEntry]
) -> KnowledgeEntry | None:
    """Find an existing active entry that contradicts the candidate.

    Conflict criteria: same affected_files overlap but different type
    (e.g. pattern vs anti_pattern for same files/tags).
    Uses pattern-based matching for file overlap detection.
    """
    CONTRADICTIONS = {
        ("pattern", "anti_pattern"),
        ("anti_pattern", "pattern"),
    }
    candidate_tags = set(candidate.tags)
    for entry in existing:
        if entry.deprecated:
            continue
        if (candidate.type, entry.type) not in CONTRADICTIONS:
            continue
        tag_overlap = candidate_tags & set(entry.tags)
        file_overlap = _patterns_overlap(candidate.affected_files, entry.affected_files)
        if tag_overlap or file_overlap:
            return entry
    return None


# ---------------------------------------------------------------------------
# Selective injection — DESIGN.md §9.3
# ---------------------------------------------------------------------------


def prime_knowledge(
    knowledge_path: str | Path,
    affected_files: list[str],
    tags: list[str],
    max_entries: int = MAX_INJECTION_ENTRIES,
) -> list[KnowledgeEntry]:
    """Select relevant knowledge entries for injection into a session.

    Scoring per DESIGN.md §9.3:
    - File match (fnmatch): +3 per matching pattern
    - Tag match: +1 per overlapping tag
    - Quality weight: score *= confidence
    - Outdated penalty: score -= outdated_reports * 0.5
    - Filter: confidence >= 0.3, not deprecated
    """
    entries = load_active_knowledge(knowledge_path)
    scored: list[tuple[float, KnowledgeEntry]] = []

    for entry in entries:
        if not entry.is_injectable:
            continue
        score = 0.0
        for pattern in entry.affected_files:
            if any(fnmatch(f, pattern) for f in affected_files):
                score += 3
        score += len(set(entry.tags) & set(tags))
        score *= entry.confidence
        score -= entry.outdated_reports * 0.5
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:max_entries]]
