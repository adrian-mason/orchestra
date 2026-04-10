"""Pre-defined tool permission profiles for Orchestra agents (advisory/policy layer).

Profiles declare which tool categories an agent is *intended* to use. They are
used for:
    - Constructing agent system prompts (instruction-level enforcement)
    - Declaring tool allowlists in orchestra.yaml
    - Computing provider-native tool blocking sets (where categories map to
      distinct native tools, e.g., fs_read vs fs_write)

**Important:** For categories that share a native tool (e.g., github_read and
github_write both map to Bash/shell), profiles express *policy intent* but
cannot enforce capability isolation at the tool-blocking level. In these cases,
enforcement relies on agent instructions. Command-level guards (e.g., wrapping
GitHub CLI operations) are planned for Phase 2.

Pre-defined profiles (DESIGN.md §2.7):
    - github_tools: Full GitHub policy (PRShepherd)
    - github_readonly_tools: Read-only GitHub policy (Critic, Challenger)
    - dev_tools: Shell + filesystem (implementation agents)
    - shell_tools: Shell execution only
    - fs_tools / fs_read_tools / fs_write_tools: Filesystem subsets
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from orchestra.tools.vocabulary import (
    ToolCategory,
    resolve_allowed_tools,
    get_disallowed_tools,
)


@dataclass(frozen=True)
class ToolProfile:
    """A named policy profile declaring which tool categories an agent is allowed to use.

    Profiles are advisory: they declare intent and are used to build agent
    instructions and tool allowlists. Where categories map to distinct native
    tools (e.g., fs_read → Read vs fs_write → Edit/Write), the profile can
    be enforced via tool blocking. Where categories share a native tool
    (e.g., github_read and github_write both → Bash), enforcement is
    instruction-level only until Phase 2 command guards are implemented.

    Attributes:
        name: Human-readable profile name (e.g., "github_tools").
        description: What this profile is for.
        categories: Allowed ToolCategory values (policy declaration).
    """

    name: str
    description: str
    categories: tuple[str, ...] = field(default_factory=tuple)
    enforced: bool = False
    """Whether this profile's restrictions are enforced at the native-tool level.

    When False (default), the profile is advisory/policy — it declares intent
    and is used to build agent instructions, but categories that share a native
    tool (e.g., github_read/write → Bash) cannot be blocked at the tool layer.

    Set to True only when command-level guards (Phase 2) can actually enforce
    the restriction at capability level.
    """

    def allowed_tools(self, provider: str) -> List[str]:
        """Resolve to provider-native tool names that are ALLOWED."""
        return resolve_allowed_tools(list(self.categories), provider)

    def disallowed_tools(self, provider: str) -> List[str]:
        """Resolve to provider-native tool names that should be BLOCKED."""
        return get_disallowed_tools(list(self.categories), provider)

    def readonly(self) -> ToolProfile:
        """Return a new profile with only read-oriented categories.

        Strips github_write, fs_write, and shell. Keeps github_read, fs_read, fs_list.
        This implements the `github_tools.readonly()` pattern from DESIGN.md §2.7.
        """
        read_categories = {
            ToolCategory.GITHUB_READ.value,
            ToolCategory.FS_READ.value,
            ToolCategory.FS_LIST.value,
        }
        filtered = tuple(c for c in self.categories if c in read_categories)
        # If the original had wildcards, expand then filter
        if not filtered and self.categories:
            from orchestra.tools.vocabulary import _expand_categories
            expanded = _expand_categories(list(self.categories))
            filtered = tuple(
                c.value for c in expanded
                if c.value in read_categories
            )
        return ToolProfile(
            name=f"{self.name}_readonly",
            description=f"Read-only subset of {self.name}",
            categories=filtered,
            enforced=False,  # readonly via category policy; not native-tool enforced
        )

    def __contains__(self, category: str) -> bool:
        """Check if a category is allowed by this profile (respects wildcards)."""
        from orchestra.tools.vocabulary import _expand_categories
        expanded = _expand_categories(list(self.categories))
        try:
            cat = ToolCategory(category)
        except ValueError:
            return False
        return cat in expanded

    def __add__(self, other: ToolProfile) -> ToolProfile:
        """Combine two profiles into a union profile."""
        combined = set(self.categories) | set(other.categories)
        return ToolProfile(
            name=f"{self.name}+{other.name}",
            description=f"Union of {self.name} and {other.name}",
            categories=tuple(sorted(combined)),
        )


# ── Pre-defined profiles ──

github_tools = ToolProfile(
    name="github_tools",
    description="Full GitHub policy: read + write (PR create/merge/push). For PRShepherd.",
    categories=(ToolCategory.GITHUB_ALL.value,),
)

github_readonly_tools = ToolProfile(
    name="github_readonly_tools",
    description="Read-only GitHub policy: PR diff, comments, CI status. For Critic/Challenger. Instruction-enforced; command-level guard planned for Phase 2.",
    categories=(ToolCategory.GITHUB_READ.value,),
)

shell_tools = ToolProfile(
    name="shell_tools",
    description="Shell/bash command execution only.",
    categories=(ToolCategory.SHELL.value,),
)

fs_read_tools = ToolProfile(
    name="fs_read_tools",
    description="File reading only.",
    categories=(ToolCategory.FS_READ.value,),
    enforced=True,  # fs_read maps to distinct native tools (e.g., Read)
)

fs_write_tools = ToolProfile(
    name="fs_write_tools",
    description="File reading + writing (includes read for safe editing).",
    categories=(ToolCategory.FS_READ.value, ToolCategory.FS_WRITE.value),
    enforced=True,  # fs_read/write map to distinct native tools
)

fs_tools = ToolProfile(
    name="fs_tools",
    description="Full filesystem access: read + write + list/search.",
    categories=(ToolCategory.FS_ALL.value,),
    enforced=True,  # all fs categories map to distinct native tools
)

dev_tools = ToolProfile(
    name="dev_tools",
    description="Full development access: shell + all filesystem. For implementation agents.",
    categories=(
        ToolCategory.SHELL.value,
        ToolCategory.FS_ALL.value,
    ),
)

# Registry for lookup by name.
PROFILES: Dict[str, ToolProfile] = {
    p.name: p
    for p in [
        github_tools,
        github_readonly_tools,
        shell_tools,
        fs_read_tools,
        fs_write_tools,
        fs_tools,
        dev_tools,
    ]
}


def get_profile(name: str) -> ToolProfile:
    """Look up a pre-defined tool profile by name.

    Args:
        name: Profile name (e.g., "github_tools", "dev_tools").

    Returns:
        The matching ToolProfile.

    Raises:
        KeyError: If the profile name is not found.
    """
    if name not in PROFILES:
        raise KeyError(
            f"Unknown tool profile '{name}'. "
            f"Known profiles: {sorted(PROFILES.keys())}"
        )
    return PROFILES[name]
