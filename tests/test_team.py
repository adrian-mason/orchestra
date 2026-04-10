"""Tests for orchestra.utils.team (AC-06)."""

import re

import pytest

from orchestra.utils.team import check_team_member_errors, TeamMemberError


class TestCheckTeamMemberErrors:
    def test_clean_content_returns_empty(self):
        result = check_team_member_errors(
            "All members completed successfully. Design approved.",
            raise_on_error=False,
        )
        assert result == []

    def test_empty_content_returns_empty(self):
        assert check_team_member_errors("", raise_on_error=False) == []

    def test_detects_traceback(self):
        content = "Member output: Traceback (most recent call last): ..."
        errors = check_team_member_errors(content, raise_on_error=False)
        assert len(errors) > 0
        assert any("Traceback" in e for e in errors)

    def test_detects_exception(self):
        content = "Analysis failed with ValueError: invalid input"
        errors = check_team_member_errors(content, raise_on_error=False)
        assert len(errors) > 0

    def test_detects_member_failed(self):
        content = "Note: member architect failed during execution"
        errors = check_team_member_errors(content, raise_on_error=False)
        assert len(errors) > 0

    def test_raises_by_default(self):
        content = "Traceback (most recent call last): some error"
        with pytest.raises(TeamMemberError, match="Team member error"):
            check_team_member_errors(content)

    def test_raise_includes_error_list(self):
        content = "Error occurred and also Exception raised"
        with pytest.raises(TeamMemberError) as exc_info:
            check_team_member_errors(content)
        assert len(exc_info.value.errors) > 0

    def test_no_raise_when_disabled(self):
        content = "Error in member output"
        result = check_team_member_errors(content, raise_on_error=False)
        assert len(result) > 0  # detected but not raised

    def test_extra_patterns(self):
        content = "CUSTOM_FAILURE_SIGNAL detected in pipeline"
        # Default patterns won't match this
        result = check_team_member_errors(content, raise_on_error=False)
        assert result == []
        # With extra pattern it should match
        result = check_team_member_errors(
            content,
            raise_on_error=False,
            extra_patterns=[re.compile(r"CUSTOM_FAILURE_SIGNAL")],
        )
        assert len(result) > 0

    def test_deduplicates_matches(self):
        # Same error appearing in overlapping contexts should be deduped
        content = "Error Error Error"
        result = check_team_member_errors(content, raise_on_error=False)
        # Should have deduplicated entries
        assert len(result) == len(set(result))
