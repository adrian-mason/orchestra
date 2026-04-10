"""Team member error checking utility (AC-06).

Agno uses error-as-content for Team member failures: when a member raises an
exception, it's caught and passed as a string to the leader. The leader may
still produce a valid-looking synthesis, masking the member failure.

This utility inspects Team responses for common error signals to prevent
silent failures from propagating.
"""

from __future__ import annotations

import re

# Patterns that indicate a member error was captured as content.
# These match common Python exception formats that Agno injects.
_ERROR_PATTERNS = [
    re.compile(r"(?:Error|Exception|Traceback)\b", re.IGNORECASE),
    re.compile(r"raise\s+\w+Error"),
    re.compile(r"member\s+\w+\s+failed", re.IGNORECASE),
    re.compile(r"error occurred during execution", re.IGNORECASE),
]


class TeamMemberError(Exception):
    """Raised when a Team response contains member error signals."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "; ".join(errors[:3])
        if len(errors) > 3:
            summary += f" (and {len(errors) - 3} more)"
        super().__init__(f"Team member error(s) detected: {summary}")


def check_team_member_errors(
    content: str,
    *,
    raise_on_error: bool = True,
    extra_patterns: list[re.Pattern[str]] | None = None,
) -> list[str]:
    """Check Team leader response content for member error signals.

    Args:
        content: The Team leader's response content string.
        raise_on_error: If True (default), raise TeamMemberError when errors
            are detected. If False, return the list of matched error strings.
        extra_patterns: Additional regex patterns to check beyond the defaults.

    Returns:
        List of matched error strings. Empty if no errors detected.

    Raises:
        TeamMemberError: If raise_on_error is True and errors are detected.
    """
    if not content:
        return []

    patterns = list(_ERROR_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    errors: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(content):
            # Extract surrounding context (up to 80 chars around match)
            start = max(0, match.start() - 40)
            end = min(len(content), match.end() + 40)
            context = content[start:end].strip()
            errors.append(context)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_errors: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique_errors.append(e)

    if unique_errors and raise_on_error:
        raise TeamMemberError(unique_errors)

    return unique_errors
