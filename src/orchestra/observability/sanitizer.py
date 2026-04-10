"""Secret redaction sanitizer for Orchestra logging.

P0-11: Implements automatic detection and irreversible redaction of sensitive
information (API keys, tokens, credentials) before log output.

All log messages MUST pass through sanitize() before being written to any
output (session.log, events.ndjson, errors.log). Redaction preserves enough
context for debugging (first 4 chars of key + replacement marker) while
ensuring the original secret cannot be recovered.

Reference: DESIGN.md §8.4, Overstory src/logging/sanitizer.ts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class RedactPattern:
    """A pattern-replacement pair for secret redaction."""

    pattern: re.Pattern[str]
    replacement: str
    description: str


# Default redaction patterns covering known API key formats.
# Order matters: more specific patterns should come before general ones
# to avoid partial matches (e.g., sk-ant-* before sk-*).
DEFAULT_PATTERNS: tuple[RedactPattern, ...] = (
    # Anthropic API keys (must precede generic sk-* pattern)
    RedactPattern(
        pattern=re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,}"),
        replacement="sk-ant-****[REDACTED_ANTHROPIC_KEY]",
        description="Anthropic API key (sk-ant-*)",
    ),
    # OpenAI API keys (sk-proj-*, sk-*)
    RedactPattern(
        pattern=re.compile(r"sk-proj-[a-zA-Z0-9\-_]{20,}"),
        replacement="sk-proj-****[REDACTED_OPENAI_PROJECT_KEY]",
        description="OpenAI project API key (sk-proj-*)",
    ),
    RedactPattern(
        pattern=re.compile(r"sk-[a-zA-Z0-9]{20,}"),
        replacement="sk-****[REDACTED_OPENAI_KEY]",
        description="OpenAI API key (sk-*)",
    ),
    # Google AI keys
    RedactPattern(
        pattern=re.compile(r"AIza[a-zA-Z0-9\-_]{30,}"),
        replacement="AIza****[REDACTED_GOOGLE_KEY]",
        description="Google API key (AIza*)",
    ),
    # GitHub tokens
    RedactPattern(
        pattern=re.compile(r"github_pat_[a-zA-Z0-9_]{20,}"),
        replacement="github_pat_****[REDACTED_GITHUB_PAT]",
        description="GitHub fine-grained PAT",
    ),
    RedactPattern(
        pattern=re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
        replacement="ghp_****[REDACTED_GITHUB_TOKEN]",
        description="GitHub personal access token (ghp_*)",
    ),
    RedactPattern(
        pattern=re.compile(r"gho_[a-zA-Z0-9]{20,}"),
        replacement="gho_****[REDACTED_GITHUB_OAUTH]",
        description="GitHub OAuth token (gho_*)",
    ),
    RedactPattern(
        pattern=re.compile(r"ghs_[a-zA-Z0-9]{20,}"),
        replacement="ghs_****[REDACTED_GITHUB_SERVER]",
        description="GitHub server token (ghs_*)",
    ),
    RedactPattern(
        pattern=re.compile(r"ghr_[a-zA-Z0-9]{20,}"),
        replacement="ghr_****[REDACTED_GITHUB_REFRESH]",
        description="GitHub refresh token (ghr_*)",
    ),
    # AWS keys
    RedactPattern(
        pattern=re.compile(r"AKIA[A-Z0-9]{16}"),
        replacement="AKIA****[REDACTED_AWS_ACCESS_KEY]",
        description="AWS access key ID",
    ),
    # Bearer tokens in Authorization headers
    RedactPattern(
        pattern=re.compile(r"Bearer\s+[a-zA-Z0-9\-_.~+/]+=*", re.IGNORECASE),
        replacement="Bearer [REDACTED]",
        description="Bearer token",
    ),
    # Basic auth in URLs (e.g., https://user:password@host)
    RedactPattern(
        pattern=re.compile(r"(https?://)([^:]+):([^@]+)@"),
        replacement=r"\1\2:[REDACTED]@",
        description="URL embedded credentials",
    ),
    # Environment variable assignments containing secrets
    RedactPattern(
        pattern=re.compile(
            r"((?:ANTHROPIC_API_KEY|OPENAI_API_KEY|GOOGLE_API_KEY|"
            r"GITHUB_TOKEN|AWS_SECRET_ACCESS_KEY|DATABASE_URL|"
            r"SECRET_KEY|API_KEY|ACCESS_TOKEN|AUTH_TOKEN)"
            r"\s*=\s*)[^\s]+"
        ),
        replacement=r"\1[REDACTED]",
        description="Environment variable secret assignment",
    ),
    # Generic long hex/base64 tokens (catch-all, conservative)
    # Only matches tokens that look like they're in a key=value or header context
    RedactPattern(
        pattern=re.compile(
            r"((?:token|key|secret|password|credential|auth)\s*[:=]\s*)"
            r"['\"]?([a-zA-Z0-9\-_.~+/]{32,}=*)['\"]?",
            re.IGNORECASE,
        ),
        replacement=r"\1[REDACTED]",
        description="Generic key/token/secret assignment",
    ),
)


@dataclass
class Sanitizer:
    """Configurable secret redaction engine.

    Usage:
        sanitizer = Sanitizer()  # uses default patterns
        safe_text = sanitizer.sanitize("My key is sk-ant-abc123...")

    Custom patterns can be added at construction or runtime:
        sanitizer = Sanitizer(extra_patterns=[RedactPattern(...)])
        sanitizer.add_pattern(RedactPattern(...))
    """

    _patterns: list[RedactPattern] = field(default_factory=list)

    def __init__(
        self,
        *,
        use_defaults: bool = True,
        extra_patterns: Sequence[RedactPattern] = (),
    ) -> None:
        if use_defaults:
            self._patterns = list(DEFAULT_PATTERNS)
        else:
            self._patterns = []
        self._patterns.extend(extra_patterns)

    def add_pattern(self, pattern: RedactPattern) -> None:
        """Add a custom redaction pattern at runtime."""
        self._patterns.append(pattern)

    def sanitize(self, text: str) -> str:
        """Apply all redaction patterns to the input text.

        Redaction is irreversible — the original secret cannot be recovered
        from the output. This function MUST be called before any log write.
        """
        for rp in self._patterns:
            text = rp.pattern.sub(rp.replacement, text)
        return text

    @property
    def patterns(self) -> list[RedactPattern]:
        """Return a copy of the current pattern list."""
        return list(self._patterns)


# Module-level singleton for convenience.
_default_sanitizer = Sanitizer()


def sanitize(text: str) -> str:
    """Redact secrets from text using the default sanitizer.

    This is the primary entry point for the redaction layer.
    All logging code should call this function before writing output.
    """
    return _default_sanitizer.sanitize(text)


def get_default_sanitizer() -> Sanitizer:
    """Return the module-level default sanitizer instance."""
    return _default_sanitizer
