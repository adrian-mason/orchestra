"""Universal tool vocabulary and provider translation layer.

Orchestra defines a universal tool vocabulary that is translated to each
provider's native tool names. This is adapted from CAO's tool_mapping.py
but extended for Orchestra's domain: GitHub operations (read/write split),
Shell execution, and FileSystem access.

**Advisory vs enforcement:** Categories that map to distinct native tools
(e.g., fs_read → Read vs fs_write → Edit/Write) support tool-level blocking.
Categories that share a native tool (e.g., github_read and github_write both
map to Bash/shell) express policy intent only — enforcement for these relies
on agent instructions. Command-level guards are planned for Phase 2.

Tool categories use a hierarchical naming convention:
    - github_read: Read-only GitHub operations (PR diff, comments, CI status)
    - github_write: Mutating GitHub operations (create PR, merge, push)
    - github_*: All GitHub operations
    - shell: Shell/bash command execution
    - fs_read: File reading
    - fs_write: File editing/writing
    - fs_list: File search (glob, grep)
    - fs_*: All filesystem operations
    - *: Unrestricted (all tools)
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, List, Set


class ToolCategory(str, Enum):
    """Orchestra universal tool categories.

    Each category maps to a set of provider-native tool names.
    Wildcard categories (ending in _ALL) are unions of their sub-categories.
    """

    # GitHub operations — split for tool isolation (DESIGN.md §2.7)
    GITHUB_READ = "github_read"
    GITHUB_WRITE = "github_write"
    GITHUB_ALL = "github_*"

    # Shell execution
    SHELL = "shell"

    # Filesystem operations
    FS_READ = "fs_read"
    FS_WRITE = "fs_write"
    FS_LIST = "fs_list"
    FS_ALL = "fs_*"

    # Unrestricted
    ALL = "*"


# Wildcard expansions: which sub-categories a wildcard includes.
_WILDCARD_EXPANSIONS: Dict[ToolCategory, FrozenSet[ToolCategory]] = {
    ToolCategory.GITHUB_ALL: frozenset({ToolCategory.GITHUB_READ, ToolCategory.GITHUB_WRITE}),
    ToolCategory.FS_ALL: frozenset({ToolCategory.FS_READ, ToolCategory.FS_WRITE, ToolCategory.FS_LIST}),
}

# All non-wildcard, non-ALL leaf categories.
_LEAF_CATEGORIES: FrozenSet[ToolCategory] = frozenset({
    ToolCategory.GITHUB_READ,
    ToolCategory.GITHUB_WRITE,
    ToolCategory.SHELL,
    ToolCategory.FS_READ,
    ToolCategory.FS_WRITE,
    ToolCategory.FS_LIST,
})


def _expand_categories(categories: List[str]) -> Set[ToolCategory]:
    """Expand a list of category names (including wildcards) to leaf categories."""
    result: Set[ToolCategory] = set()
    for name in categories:
        cat = ToolCategory(name)
        if cat == ToolCategory.ALL:
            return set(_LEAF_CATEGORIES)
        if cat in _WILDCARD_EXPANSIONS:
            result.update(_WILDCARD_EXPANSIONS[cat])
        else:
            result.add(cat)
    return result


# ── Provider-native tool mappings ──
#
# Keys are provider names, values map ToolCategory to lists of native tool names.
# This extends CAO's mapping with GitHub read/write split.
#
# NOTE: github_read and github_write map to the same native shell tool because
# GitHub operations go through CLI commands (gh, git). The read/write distinction
# is policy-level only at Phase 0; Phase 2 will introduce command-level wrappers
# that can enforce the split at capability level.

TOOL_MAPPING: Dict[str, Dict[str, List[str]]] = {
    "claude_code": {
        "github_read": ["Bash"],  # gh pr view, gh api (read-only via instructions)
        "github_write": ["Bash"],  # gh pr create, gh pr merge, git push
        "shell": ["Bash"],
        "fs_read": ["Read"],
        "fs_write": ["Edit", "Write"],
        "fs_list": ["Glob", "Grep"],
    },
    "copilot_cli": {
        "github_read": ["shell"],
        "github_write": ["shell"],
        "shell": ["shell"],
        "fs_read": ["read"],
        "fs_write": ["write"],
        "fs_list": ["list", "grep"],
    },
    "gemini_cli": {
        "github_read": ["run_shell_command"],
        "github_write": ["run_shell_command"],
        "shell": ["run_shell_command"],
        "fs_read": ["read_file"],
        "fs_write": ["write_file", "replace"],
        "fs_list": ["list_directory", "glob", "search_file_content"],
    },
}

# Complete set of all native tools per provider.
ALL_NATIVE_TOOLS: Dict[str, FrozenSet[str]] = {}
for _provider, _mapping in TOOL_MAPPING.items():
    _tools: Set[str] = set()
    for _native_list in _mapping.values():
        _tools.update(_native_list)
    ALL_NATIVE_TOOLS[_provider] = frozenset(_tools)


def resolve_allowed_tools(
    categories: List[str],
    provider: str,
) -> List[str]:
    """Resolve Orchestra tool categories to provider-native tool names that are ALLOWED.

    Args:
        categories: List of Orchestra tool category names (e.g., ["github_read", "fs_*"]).
        provider: Provider name (e.g., "claude_code").

    Returns:
        Sorted list of provider-native tool names that are allowed.

    Raises:
        ValueError: If provider is unknown or a category name is invalid.
    """
    mapping = TOOL_MAPPING.get(provider)
    if mapping is None:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Known providers: {sorted(TOOL_MAPPING.keys())}"
        )

    expanded = _expand_categories(categories)
    allowed: Set[str] = set()
    for cat in expanded:
        native = mapping.get(cat.value, [])
        allowed.update(native)

    return sorted(allowed)


def get_disallowed_tools(
    categories: List[str],
    provider: str,
) -> List[str]:
    """Given allowed Orchestra categories, return provider-native tools to BLOCK.

    This is the inverse of resolve_allowed_tools: everything in the provider's
    full tool set that is NOT allowed should be blocked.

    Args:
        categories: List of Orchestra tool category names that are ALLOWED.
        provider: Provider name.

    Returns:
        Sorted list of provider-native tool names that should be BLOCKED.

    Raises:
        ValueError: If provider is unknown or a category name is invalid.
    """
    if "*" in categories:
        return []

    mapping = TOOL_MAPPING.get(provider)
    if mapping is None:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Known providers: {sorted(TOOL_MAPPING.keys())}"
        )

    allowed_native = set(resolve_allowed_tools(categories, provider))
    all_tools = ALL_NATIVE_TOOLS.get(provider, frozenset())
    return sorted(all_tools - allowed_native)
