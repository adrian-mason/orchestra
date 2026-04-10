"""Tests for orchestra.tools — tool vocabulary, provider translation, and profiles."""

import pytest

from orchestra.tools.vocabulary import (
    ToolCategory,
    TOOL_MAPPING,
    ALL_NATIVE_TOOLS,
    resolve_allowed_tools,
    get_disallowed_tools,
    _expand_categories,
)
from orchestra.tools.profiles import (
    ToolProfile,
    PROFILES,
    get_profile,
    github_tools,
    github_readonly_tools,
    shell_tools,
    fs_read_tools,
    fs_write_tools,
    fs_tools,
    dev_tools,
)


# ── ToolCategory enum ──


class TestToolCategory:
    def test_all_leaf_categories_present(self):
        leaves = {"github_read", "github_write", "shell", "fs_read", "fs_write", "fs_list"}
        for name in leaves:
            assert ToolCategory(name) is not None

    def test_wildcard_categories(self):
        assert ToolCategory("github_*") == ToolCategory.GITHUB_ALL
        assert ToolCategory("fs_*") == ToolCategory.FS_ALL
        assert ToolCategory("*") == ToolCategory.ALL

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError):
            ToolCategory("nonexistent")


# ── _expand_categories ──


class TestExpandCategories:
    def test_leaf_category_unchanged(self):
        result = _expand_categories(["github_read"])
        assert result == {ToolCategory.GITHUB_READ}

    def test_github_wildcard_expands(self):
        result = _expand_categories(["github_*"])
        assert result == {ToolCategory.GITHUB_READ, ToolCategory.GITHUB_WRITE}

    def test_fs_wildcard_expands(self):
        result = _expand_categories(["fs_*"])
        assert result == {ToolCategory.FS_READ, ToolCategory.FS_WRITE, ToolCategory.FS_LIST}

    def test_all_wildcard_returns_all_leaves(self):
        result = _expand_categories(["*"])
        assert ToolCategory.GITHUB_READ in result
        assert ToolCategory.GITHUB_WRITE in result
        assert ToolCategory.SHELL in result
        assert ToolCategory.FS_READ in result
        assert ToolCategory.FS_WRITE in result
        assert ToolCategory.FS_LIST in result

    def test_mixed_leaf_and_wildcard(self):
        result = _expand_categories(["shell", "fs_*"])
        assert ToolCategory.SHELL in result
        assert ToolCategory.FS_READ in result
        assert ToolCategory.FS_WRITE in result
        assert ToolCategory.FS_LIST in result
        assert ToolCategory.GITHUB_READ not in result

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError):
            _expand_categories(["bad_category"])


# ── TOOL_MAPPING structure ──


class TestToolMapping:
    def test_all_three_providers_present(self):
        assert "claude_code" in TOOL_MAPPING
        assert "copilot_cli" in TOOL_MAPPING
        assert "gemini_cli" in TOOL_MAPPING

    def test_all_leaf_categories_mapped_per_provider(self):
        leaves = ["github_read", "github_write", "shell", "fs_read", "fs_write", "fs_list"]
        for provider, mapping in TOOL_MAPPING.items():
            for cat in leaves:
                assert cat in mapping, f"{provider} missing mapping for {cat}"

    def test_claude_code_mapping_values(self):
        m = TOOL_MAPPING["claude_code"]
        assert "Bash" in m["shell"]
        assert "Read" in m["fs_read"]
        assert "Edit" in m["fs_write"]
        assert "Write" in m["fs_write"]
        assert "Glob" in m["fs_list"]
        assert "Grep" in m["fs_list"]

    def test_all_native_tools_computed(self):
        for provider in TOOL_MAPPING:
            assert provider in ALL_NATIVE_TOOLS
            assert len(ALL_NATIVE_TOOLS[provider]) > 0

    def test_all_native_tools_is_frozenset(self):
        for provider in ALL_NATIVE_TOOLS:
            assert isinstance(ALL_NATIVE_TOOLS[provider], frozenset)


# ── resolve_allowed_tools ──


class TestResolveAllowedTools:
    def test_single_category(self):
        result = resolve_allowed_tools(["fs_read"], "claude_code")
        assert result == ["Read"]

    def test_multiple_categories(self):
        result = resolve_allowed_tools(["fs_read", "fs_list"], "claude_code")
        assert set(result) == {"Read", "Glob", "Grep"}

    def test_wildcard_category(self):
        result = resolve_allowed_tools(["fs_*"], "claude_code")
        assert set(result) == {"Read", "Edit", "Write", "Glob", "Grep"}

    def test_all_wildcard(self):
        result = resolve_allowed_tools(["*"], "claude_code")
        assert set(result) == ALL_NATIVE_TOOLS["claude_code"]

    def test_github_read_only(self):
        result = resolve_allowed_tools(["github_read"], "claude_code")
        assert "Bash" in result

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            resolve_allowed_tools(["shell"], "unknown_provider")

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError):
            resolve_allowed_tools(["nonexistent"], "claude_code")

    def test_gemini_provider(self):
        result = resolve_allowed_tools(["fs_read"], "gemini_cli")
        assert "read_file" in result

    def test_copilot_provider(self):
        result = resolve_allowed_tools(["fs_write"], "copilot_cli")
        assert "write" in result


# ── get_disallowed_tools ──


class TestGetDisallowedTools:
    def test_all_wildcard_blocks_nothing(self):
        result = get_disallowed_tools(["*"], "claude_code")
        assert result == []

    def test_fs_read_blocks_write_tools(self):
        result = get_disallowed_tools(["fs_read"], "claude_code")
        # fs_read allows Read; everything else should be blocked
        assert "Edit" in result
        assert "Write" in result
        assert "Read" not in result

    def test_complementary_with_resolve(self):
        """allowed + disallowed = ALL_NATIVE_TOOLS."""
        categories = ["shell", "fs_read"]
        allowed = set(resolve_allowed_tools(categories, "claude_code"))
        disallowed = set(get_disallowed_tools(categories, "claude_code"))
        assert allowed | disallowed == ALL_NATIVE_TOOLS["claude_code"]
        assert allowed & disallowed == set()

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_disallowed_tools(["shell"], "unknown_provider")


# ── ToolProfile ──


class TestToolProfile:
    def test_profile_creation(self):
        p = ToolProfile(name="test", description="test profile", categories=("shell",))
        assert p.name == "test"
        assert "shell" in p.categories

    def test_allowed_tools(self):
        p = ToolProfile(name="test", description="", categories=("fs_read", "fs_list"))
        result = p.allowed_tools("claude_code")
        assert set(result) == {"Read", "Glob", "Grep"}

    def test_disallowed_tools(self):
        p = ToolProfile(name="test", description="", categories=("fs_read",))
        result = p.disallowed_tools("claude_code")
        assert "Read" not in result
        assert "Bash" in result

    def test_readonly_strips_write(self):
        full = ToolProfile(
            name="full",
            description="",
            categories=("github_*", "fs_*"),
        )
        ro = full.readonly()
        assert "github_read" in ro.categories
        assert "github_write" not in ro.categories
        assert "fs_read" in ro.categories
        assert "fs_write" not in ro.categories
        assert "fs_list" in ro.categories

    def test_readonly_name(self):
        p = ToolProfile(name="my_tools", description="", categories=("github_*",))
        ro = p.readonly()
        assert ro.name == "my_tools_readonly"

    def test_contains_direct(self):
        p = ToolProfile(name="test", description="", categories=("shell", "fs_read"))
        assert "shell" in p
        assert "fs_read" in p
        assert "fs_write" not in p

    def test_contains_wildcard(self):
        p = ToolProfile(name="test", description="", categories=("fs_*",))
        assert "fs_read" in p
        assert "fs_write" in p
        assert "fs_list" in p
        assert "shell" not in p

    def test_contains_invalid_category(self):
        p = ToolProfile(name="test", description="", categories=("shell",))
        assert "nonexistent" not in p

    def test_add_profiles(self):
        combined = shell_tools + fs_read_tools
        assert "shell" in combined
        assert "fs_read" in combined

    def test_frozen(self):
        with pytest.raises(AttributeError):
            github_tools.name = "hacked"  # type: ignore[misc]


# ── Pre-defined profiles ──


class TestPreDefinedProfiles:
    def test_github_tools_has_full_access(self):
        assert "github_read" in github_tools
        assert "github_write" in github_tools

    def test_github_readonly_tools_no_write(self):
        assert "github_read" in github_readonly_tools
        assert "github_write" not in github_readonly_tools

    def test_github_readonly_matches_design_pattern(self):
        """DESIGN.md §2.7: github_readonly_tools = github_tools.readonly()"""
        derived = github_tools.readonly()
        assert "github_read" in derived
        assert "github_write" not in derived
        # Both should resolve to the same native tools
        assert (
            derived.allowed_tools("claude_code")
            == github_readonly_tools.allowed_tools("claude_code")
        )

    def test_shell_tools(self):
        assert "shell" in shell_tools
        assert "fs_read" not in shell_tools

    def test_fs_read_tools(self):
        assert "fs_read" in fs_read_tools
        assert "fs_write" not in fs_read_tools

    def test_fs_write_tools_includes_read(self):
        """Write profile includes read for safe editing."""
        assert "fs_read" in fs_write_tools
        assert "fs_write" in fs_write_tools
        assert "fs_list" not in fs_write_tools

    def test_fs_tools_has_all_fs(self):
        assert "fs_read" in fs_tools
        assert "fs_write" in fs_tools
        assert "fs_list" in fs_tools
        assert "shell" not in fs_tools

    def test_dev_tools_has_shell_and_fs(self):
        assert "shell" in dev_tools
        assert "fs_read" in dev_tools
        assert "fs_write" in dev_tools
        assert "fs_list" in dev_tools
        assert "github_read" not in dev_tools

    def test_all_profiles_in_registry(self):
        expected = {
            "github_tools", "github_readonly_tools", "shell_tools",
            "fs_read_tools", "fs_write_tools", "fs_tools", "dev_tools",
        }
        assert set(PROFILES.keys()) == expected


# ── get_profile ──


class TestGetProfile:
    def test_known_profile(self):
        p = get_profile("github_tools")
        assert p is github_tools

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError, match="Unknown tool profile"):
            get_profile("nonexistent_profile")


# ── Cross-provider consistency ──


class TestCrossProviderConsistency:
    """Verify that the same categories resolve to non-empty tool sets across providers."""

    @pytest.mark.parametrize("provider", list(TOOL_MAPPING.keys()))
    def test_all_leaf_categories_resolve(self, provider):
        for cat in ["github_read", "github_write", "shell", "fs_read", "fs_write", "fs_list"]:
            result = resolve_allowed_tools([cat], provider)
            assert len(result) > 0, f"{cat} resolved to empty for {provider}"

    @pytest.mark.parametrize("provider", list(TOOL_MAPPING.keys()))
    def test_wildcard_covers_all_native_tools(self, provider):
        result = set(resolve_allowed_tools(["*"], provider))
        assert result == ALL_NATIVE_TOOLS[provider]

    @pytest.mark.parametrize("provider", list(TOOL_MAPPING.keys()))
    def test_disallowed_complement(self, provider):
        """For any subset, allowed + disallowed = all native tools."""
        categories = ["github_read", "fs_read"]
        allowed = set(resolve_allowed_tools(categories, provider))
        disallowed = set(get_disallowed_tools(categories, provider))
        assert allowed | disallowed == ALL_NATIVE_TOOLS[provider]


# ── Tool policy correctness (DESIGN.md §2.7) ──
#
# These tests verify that profiles declare the correct *policy* categories.
# For github_read/github_write, this is advisory (both map to the same native
# shell tool). Capability-level enforcement is planned for Phase 2 command guards.


class TestToolPolicy:
    """Verify that tool profiles declare correct policy per DESIGN.md §2.7."""

    def test_critic_policy_excludes_write(self):
        """Critic's profile declares github_read only, not github_write."""
        assert "github_write" not in github_readonly_tools

    def test_challenger_policy_excludes_write(self):
        """Challenger's profile declares github_read only, not github_write."""
        assert "github_write" not in github_readonly_tools

    def test_pr_shepherd_policy_includes_full_github(self):
        """PRShepherd's profile declares both read and write."""
        assert "github_read" in github_tools
        assert "github_write" in github_tools

    def test_readonly_categories_are_subset(self):
        """readonly profile categories are a subset of full profile categories."""
        from orchestra.tools.vocabulary import _expand_categories
        full_cats = _expand_categories(list(github_tools.categories))
        readonly_cats = _expand_categories(list(github_readonly_tools.categories))
        assert readonly_cats < full_cats  # strict subset

    def test_github_read_write_share_native_tool(self):
        """Document: github_read and github_write map to the same native tools.

        This is expected — the read/write distinction is policy-level only.
        Capability enforcement requires Phase 2 command-level guards.
        """
        for provider in TOOL_MAPPING:
            read_tools = set(resolve_allowed_tools(["github_read"], provider))
            write_tools = set(resolve_allowed_tools(["github_write"], provider))
            assert read_tools == write_tools, (
                f"If github_read/write resolve differently for {provider}, "
                f"update this test — capability enforcement may now be possible"
            )

    def test_fs_read_write_have_distinct_native_tools(self):
        """fs_read and fs_write map to different native tools — enforcement works."""
        read_tools = set(resolve_allowed_tools(["fs_read"], "claude_code"))
        write_tools = set(resolve_allowed_tools(["fs_write"], "claude_code"))
        assert read_tools != write_tools
        assert not read_tools & write_tools  # no overlap for claude_code

    def test_github_profiles_are_advisory(self):
        """GitHub profiles are advisory (enforced=False) — shared native tool."""
        assert github_tools.enforced is False
        assert github_readonly_tools.enforced is False

    def test_fs_profiles_are_enforced(self):
        """Filesystem profiles are enforced — distinct native tools per category."""
        assert fs_read_tools.enforced is True
        assert fs_write_tools.enforced is True
        assert fs_tools.enforced is True

    def test_readonly_derived_profile_is_advisory(self):
        """readonly() produces an advisory profile."""
        ro = github_tools.readonly()
        assert ro.enforced is False
