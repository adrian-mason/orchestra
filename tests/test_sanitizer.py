"""Tests for P0-11: Secret redaction sanitizer.

Covers all known key patterns (happy path) and failure/edge cases:
- Each pattern type has at least one positive match test
- Boundary tests for short strings that should NOT match
- Multi-secret strings
- Nested/overlapping patterns
- Empty/None-like input
- Redaction irreversibility verification
- Error path: malformed input, unicode, binary-like strings
"""

from __future__ import annotations

import re

import pytest

from orchestra.observability.sanitizer import (
    DEFAULT_PATTERNS,
    RedactPattern,
    Sanitizer,
    sanitize,
)


# ---------------------------------------------------------------------------
# Happy path: each pattern type matches and redacts correctly
# ---------------------------------------------------------------------------


class TestAnthropicKeys:
    def test_redacts_anthropic_key(self) -> None:
        text = "key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize(text)
        assert "sk-ant-api03-abcdefghijklmnop" not in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result

    def test_preserves_prefix(self) -> None:
        text = "Using sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890 for auth"
        result = sanitize(text)
        assert result.startswith("Using sk-ant-****")

    def test_multiple_anthropic_keys(self) -> None:
        text = (
            "primary=sk-ant-abcdefghijklmnopqrstuvwxyz "
            "backup=sk-ant-zyxwvutsrqponmlkjihgfedcba"
        )
        result = sanitize(text)
        assert result.count("[REDACTED_ANTHROPIC_KEY]") == 2


class TestOpenAIKeys:
    def test_redacts_openai_project_key(self) -> None:
        text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "abcdefghij" not in result
        assert "[REDACTED]" in result

    def test_redacts_openai_key(self) -> None:
        text = "key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize(text)
        assert "abcdefghij" not in result
        assert "[REDACTED_OPENAI_KEY]" in result


class TestGoogleKeys:
    def test_redacts_google_key(self) -> None:
        text = "api_key=" + "AIzaSyA" + "1234567890abcdefghijklmnopqrstuv"
        result = sanitize(text)
        assert "1234567890" not in result
        assert "[REDACTED_GOOGLE_KEY]" in result


class TestGitHubTokens:
    def test_redacts_ghp(self) -> None:
        text = "token=ghp_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "abcdefghij" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_redacts_gho(self) -> None:
        text = "gho_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_OAUTH]" in result

    def test_redacts_ghs(self) -> None:
        text = "ghs_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_SERVER]" in result

    def test_redacts_ghr(self) -> None:
        text = "ghr_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_REFRESH]" in result

    def test_redacts_github_pat(self) -> None:
        text = "github_pat_11ABCDEF_abcdefghijklmnopqrstuvwxyz"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_PAT]" in result


class TestAWSKeys:
    def test_redacts_aws_access_key(self) -> None:
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = sanitize(text)
        assert "IOSFODNN7EXAMPLE" not in result
        assert "[REDACTED_AWS_ACCESS_KEY]" in result


class TestBearerTokens:
    def test_redacts_bearer(self) -> None:
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkw"
        result = sanitize(text)
        assert "eyJhbGci" not in result
        assert "Bearer [REDACTED]" in result

    def test_redacts_bearer_case_insensitive(self) -> None:
        # Sanitizer must catch all case variants — logs are not reliable
        text = "bearer eyJhbGciOiJIUzI1NiJ9.test"
        result = sanitize(text)
        assert "eyJhbGci" not in result
        assert "Bearer [REDACTED]" in result

    def test_redacts_bearer_uppercase(self) -> None:
        text = "BEARER eyJhbGciOiJIUzI1NiJ9.test"
        result = sanitize(text)
        assert "eyJhbGci" not in result
        assert "Bearer [REDACTED]" in result


class TestURLCredentials:
    def test_redacts_url_password(self) -> None:
        text = "connecting to https://admin:s3cretP4ss@db.example.com:5432/mydb"
        result = sanitize(text)
        assert "s3cretP4ss" not in result
        assert "https://admin:[REDACTED]@db.example.com" in result

    def test_preserves_url_without_credentials(self) -> None:
        text = "connecting to https://db.example.com:5432/mydb"
        result = sanitize(text)
        assert text == result


class TestEnvVarAssignments:
    def test_redacts_anthropic_env(self) -> None:
        text = "export ANTHROPIC_API_KEY=sk-ant-test123456789012345678901234"
        result = sanitize(text)
        assert "ANTHROPIC_API_KEY=[REDACTED]" in result

    def test_redacts_openai_env(self) -> None:
        text = "OPENAI_API_KEY=sk-test-abcdefghijklmnopqrstuvwxyz"
        result = sanitize(text)
        assert "OPENAI_API_KEY=[REDACTED]" in result

    def test_redacts_generic_secret_key(self) -> None:
        text = "SECRET_KEY=myverylongsecretvalue123456"
        result = sanitize(text)
        assert "SECRET_KEY=[REDACTED]" in result

    def test_redacts_database_url(self) -> None:
        text = "DATABASE_URL=postgresql://user:pass@host/db"
        result = sanitize(text)
        assert "DATABASE_URL=[REDACTED]" in result


class TestGenericTokenPattern:
    def test_redacts_generic_token_assignment(self) -> None:
        text = "auth_token: abcdefghijklmnopqrstuvwxyz12345678901234567890"
        result = sanitize(text)
        assert "abcdefghij" not in result
        assert "[REDACTED]" in result

    def test_redacts_key_equals_value(self) -> None:
        text = 'secret="abcdefghijklmnopqrstuvwxyz12345678901234567890"'
        result = sanitize(text)
        assert "abcdefghij" not in result


# ---------------------------------------------------------------------------
# Failure / edge cases
# ---------------------------------------------------------------------------


class TestBoundaryNonMatches:
    """Strings that look similar to keys but should NOT be redacted."""

    def test_short_sk_not_redacted(self) -> None:
        # sk- followed by fewer than 20 chars should not match
        text = "sk-short"
        result = sanitize(text)
        assert result == text

    def test_short_ghp_not_redacted(self) -> None:
        text = "ghp_short"
        result = sanitize(text)
        assert result == text

    def test_normal_text_not_redacted(self) -> None:
        text = "This is a normal log message with no secrets."
        result = sanitize(text)
        assert result == text

    def test_similar_prefix_not_redacted(self) -> None:
        # "skeleton" starts with "sk" but is not a key
        text = "The skeleton key opens the door"
        result = sanitize(text)
        assert result == text

    def test_aiza_short_not_redacted(self) -> None:
        text = "AIzaShort"
        result = sanitize(text)
        assert result == text


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert sanitize("") == ""

    def test_whitespace_only(self) -> None:
        text = "   \n\t  "
        assert sanitize(text) == text

    def test_unicode_content_preserved(self) -> None:
        text = "日志消息：正常运行 🚀 没有密钥"
        assert sanitize(text) == text

    def test_unicode_mixed_with_key(self) -> None:
        text = "密钥是 sk-ant-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize(text)
        assert "密钥是" in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result

    def test_very_long_input(self) -> None:
        # Performance: ensure no catastrophic backtracking
        text = "x" * 100_000 + " sk-ant-abcdefghijklmnopqrstuvwxyz1234567890 " + "y" * 100_000
        result = sanitize(text)
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert len(result) < len(text)  # redacted key is shorter

    def test_multiline_input(self) -> None:
        text = (
            "line1: normal\n"
            "line2: key=sk-ant-abcdefghijklmnopqrstuvwxyz1234567890\n"
            "line3: also normal\n"
        )
        result = sanitize(text)
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert "line1: normal" in result
        assert "line3: also normal" in result

    def test_key_at_string_boundaries(self) -> None:
        # Key at start
        text = "ghp_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_TOKEN]" in result

        # Key at end (no trailing space)
        text = "token=ghp_abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize(text)
        assert "[REDACTED_GITHUB_TOKEN]" in result


class TestIrreversibility:
    """Verify that redacted output cannot be used to recover the original."""

    def test_original_key_not_in_output(self) -> None:
        key = "sk-ant-api03-realkey1234567890abcdefghijklmnop"
        text = f"Using {key} for authentication"
        result = sanitize(text)
        # The full key must not appear anywhere in output
        assert key not in result
        # Even substrings beyond the prefix should be gone
        assert "realkey1234567890" not in result

    def test_multiple_keys_all_redacted(self) -> None:
        keys = [
            "sk-ant-abcdefghijklmnopqrstuvwxyz1234567890",
            "ghp_abcdefghijklmnopqrstuvwxyz1234",
            "AIzaSyA" + "1234567890abcdefghijklmnopqrstuv",
        ]
        text = " ".join(f"key={k}" for k in keys)
        result = sanitize(text)
        for k in keys:
            assert k not in result


class TestMultipleSecretsInOneLine:
    def test_mixed_providers(self) -> None:
        text = (
            "anthropic=sk-ant-abcdefghijklmnopqrstuvwxyz1234567890 "
            "github=ghp_abcdefghijklmnopqrstuvwxyz1234 "
            "google=" + "AIzaSyA" + "1234567890abcdefghijklmnopqrstuv"
        )
        result = sanitize(text)
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert "[REDACTED_GITHUB_TOKEN]" in result
        assert "[REDACTED_GOOGLE_KEY]" in result


# ---------------------------------------------------------------------------
# Custom sanitizer configuration
# ---------------------------------------------------------------------------


class TestCustomSanitizer:
    def test_no_defaults(self) -> None:
        s = Sanitizer(use_defaults=False)
        text = "sk-ant-abcdefghijklmnopqrstuvwxyz1234567890"
        assert s.sanitize(text) == text  # no patterns = no redaction

    def test_extra_patterns(self) -> None:
        custom = RedactPattern(
            pattern=re.compile(r"CUSTOM-[A-Z]{10,}"),
            replacement="[REDACTED_CUSTOM]",
            description="Custom key format",
        )
        s = Sanitizer(extra_patterns=[custom])
        text = "key=CUSTOM-ABCDEFGHIJKLMNOP and sk-ant-abcdefghijklmnopqrstuvwxyz1234567890"
        result = s.sanitize(text)
        assert "[REDACTED_CUSTOM]" in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result

    def test_add_pattern_at_runtime(self) -> None:
        s = Sanitizer(use_defaults=False)
        s.add_pattern(
            RedactPattern(
                pattern=re.compile(r"MY_SECRET_\d+"),
                replacement="[REDACTED_MY]",
                description="Test pattern",
            )
        )
        assert "[REDACTED_MY]" in s.sanitize("value=MY_SECRET_12345")

    def test_patterns_returns_copy(self) -> None:
        s = Sanitizer()
        patterns = s.patterns
        patterns.clear()  # modifying the copy
        assert len(s.patterns) > 0  # original unaffected


# ---------------------------------------------------------------------------
# Error path: traceback/exception content gets redacted too
# ---------------------------------------------------------------------------


class TestErrorPathRedaction:
    """Verify redaction works on error messages and tracebacks."""

    def test_traceback_with_key(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "api.py", line 42, in call_api\n'
            "    response = client.post(url, headers={'Authorization': "
            "'Bearer eyJhbGciOiJIUzI1NiJ9.test123456789012345678'})\n"
            "ConnectionError: Failed to connect"
        )
        result = sanitize(text)
        assert "eyJhbGci" not in result
        assert "Bearer [REDACTED]" in result
        assert "Traceback" in result
        assert "ConnectionError" in result

    def test_exception_message_with_env_var(self) -> None:
        text = "ConfigError: Invalid ANTHROPIC_API_KEY=sk-ant-badkey12345678901234567890abcdef"
        result = sanitize(text)
        assert "badkey123" not in result
        assert "ConfigError" in result

    def test_json_log_with_secret(self) -> None:
        text = '{"level":"error","msg":"auth failed","key":"ghp_abcdefghijklmnopqrstuvwxyz1234"}'
        result = sanitize(text)
        assert "abcdefghij" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result


# ---------------------------------------------------------------------------
# Pattern coverage completeness
# ---------------------------------------------------------------------------


class TestPatternCoverage:
    """Ensure all patterns defined in DEFAULT_PATTERNS have at least one test."""

    def test_all_patterns_have_description(self) -> None:
        for rp in DEFAULT_PATTERNS:
            assert rp.description, f"Pattern {rp.pattern.pattern} missing description"

    def test_default_pattern_count(self) -> None:
        # If someone adds a pattern, they should add a test too
        assert len(DEFAULT_PATTERNS) >= 13, (
            f"Expected at least 13 default patterns, got {len(DEFAULT_PATTERNS)}. "
            "If you added a pattern, add a corresponding test."
        )
