"""Tests for orchestra.agents.factory (P1-02).

Tests cover:
- resolve_design_members always includes Architect
- Tag-to-specialist mapping for all 4 tags
- Multi-tag projects
- Unknown tags handled gracefully (logged, not crashed)
- Empty tags list
- Duplicate tag deduplication
- Non-string tags
- Non-list project_tags
- create_member_factory closure
- list_available_tags
- Custom tag mappings
- SpecialistMapping dataclass
"""

import logging

import pytest

from orchestra.agents.factory import (
    DEFAULT_TAG_MAPPINGS,
    SpecialistMapping,
    create_member_factory,
    list_available_tags,
    resolve_design_members,
)


class TestSpecialistMapping:
    def test_frozen(self):
        m = SpecialistMapping(tag="test", name="Test", role_description="Tester")
        with pytest.raises(AttributeError):
            m.tag = "changed"  # type: ignore[misc]

    def test_default_extra_instructions(self):
        m = SpecialistMapping(tag="test", name="Test", role_description="Tester")
        assert m.extra_instructions == []

    def test_with_extra_instructions(self):
        m = SpecialistMapping(
            tag="test", name="Test", role_description="Tester",
            extra_instructions=["Focus on testing."],
        )
        assert m.extra_instructions == ["Focus on testing."]


class TestDefaultTagMappings:
    def test_has_four_mappings(self):
        assert len(DEFAULT_TAG_MAPPINGS) == 4

    def test_tag_names(self):
        tags = [m.tag for m in DEFAULT_TAG_MAPPINGS]
        assert "ebpf" in tags
        assert "frontend" in tags
        assert "data" in tags
        assert "security" in tags

    def test_specialist_names(self):
        names = {m.tag: m.name for m in DEFAULT_TAG_MAPPINGS}
        assert names["ebpf"] == "Probe"
        assert names["frontend"] == "Artisan"
        assert names["data"] == "Oracle"
        assert names["security"] == "Sentinel"

    def test_all_have_descriptions(self):
        for m in DEFAULT_TAG_MAPPINGS:
            assert len(m.role_description) > 0

    def test_all_have_extra_instructions(self):
        for m in DEFAULT_TAG_MAPPINGS:
            assert len(m.extra_instructions) > 0


class TestResolveDesignMembers:
    """Tests for resolve_design_members callable factory."""

    def test_always_includes_architect(self):
        """Architect is always the first member, regardless of tags."""
        members = resolve_design_members(None, {"project_tags": []})
        assert len(members) >= 1
        assert members[0]["name"] == "Architect"

    def test_architect_only_with_no_tags(self):
        """No tags → only Architect returned."""
        members = resolve_design_members(None, {})
        assert len(members) == 1
        assert members[0]["name"] == "Architect"

    def test_architect_only_with_empty_tags(self):
        members = resolve_design_members(None, {"project_tags": []})
        assert len(members) == 1
        assert members[0]["name"] == "Architect"

    def test_single_tag_ebpf(self):
        members = resolve_design_members(None, {"project_tags": ["ebpf"]})
        assert len(members) == 2
        assert members[0]["name"] == "Architect"
        assert members[1]["name"] == "Probe"
        assert members[1]["role"] == "eBPF Domain Expert"

    def test_single_tag_frontend(self):
        members = resolve_design_members(None, {"project_tags": ["frontend"]})
        assert len(members) == 2
        assert members[1]["name"] == "Artisan"

    def test_single_tag_data(self):
        members = resolve_design_members(None, {"project_tags": ["data"]})
        assert len(members) == 2
        assert members[1]["name"] == "Oracle"

    def test_single_tag_security(self):
        members = resolve_design_members(None, {"project_tags": ["security"]})
        assert len(members) == 2
        assert members[1]["name"] == "Sentinel"

    def test_multi_tag_project(self):
        """Multiple tags load multiple specialists."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf", "security"]}
        )
        assert len(members) == 3
        names = [m["name"] for m in members]
        assert names[0] == "Architect"
        assert "Probe" in names
        assert "Sentinel" in names

    def test_all_four_tags(self):
        """All 4 tags load all 4 specialists + Architect."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf", "frontend", "data", "security"]}
        )
        assert len(members) == 5
        names = [m["name"] for m in members]
        assert names[0] == "Architect"
        assert "Probe" in names
        assert "Artisan" in names
        assert "Oracle" in names
        assert "Sentinel" in names

    def test_unknown_tag_ignored(self, caplog):
        """Unknown tags are logged and skipped, not crashed."""
        with caplog.at_level(logging.INFO):
            members = resolve_design_members(
                None, {"project_tags": ["blockchain"]}
            )
        assert len(members) == 1  # only Architect
        assert "Unknown project tag" in caplog.text
        assert "blockchain" in caplog.text

    def test_mixed_known_and_unknown_tags(self, caplog):
        """Known tags load specialists, unknown tags are skipped."""
        with caplog.at_level(logging.INFO):
            members = resolve_design_members(
                None, {"project_tags": ["ebpf", "quantum", "security"]}
            )
        assert len(members) == 3  # Architect + Probe + Sentinel
        assert "quantum" in caplog.text

    def test_duplicate_tags_deduplicated(self):
        """Duplicate tags don't create duplicate specialists."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf", "ebpf", "ebpf"]}
        )
        assert len(members) == 2  # Architect + one Probe

    def test_case_insensitive_tags(self):
        """Tags are normalized to lowercase."""
        members = resolve_design_members(
            None, {"project_tags": ["EBPF", "Frontend"]}
        )
        assert len(members) == 3
        names = [m["name"] for m in members]
        assert "Probe" in names
        assert "Artisan" in names

    def test_non_list_project_tags_handled(self, caplog):
        """Non-list project_tags treated as empty with warning."""
        with caplog.at_level(logging.WARNING):
            members = resolve_design_members(
                None, {"project_tags": "ebpf"}
            )
        assert len(members) == 1  # only Architect
        assert "should be a list" in caplog.text

    def test_non_string_tags_skipped(self, caplog):
        """Non-string elements in tags list are skipped with warning."""
        with caplog.at_level(logging.WARNING):
            members = resolve_design_members(
                None, {"project_tags": [123, "ebpf"]}
            )
        assert len(members) == 2  # Architect + Probe
        assert "non-string tag" in caplog.text.lower()

    def test_specialists_use_specialist_base_model(self):
        """Specialists resolve through the Specialist role's default model."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf"]}
        )
        specialist = members[1]
        assert specialist["model"] == "claude-sonnet-4-6"  # Specialist default

    def test_architect_uses_architect_model(self):
        """Architect resolves to its own default model."""
        members = resolve_design_members(None, {})
        assert members[0]["model"] == "claude-opus-4-6"  # Architect default

    def test_specialist_has_extra_instructions(self):
        """Specialist agents get domain-specific instructions appended."""
        members = resolve_design_members(
            None, {"project_tags": ["security"]}
        )
        sentinel = members[1]
        assert "security" in sentinel["instructions"].lower()
        assert "threat" in sentinel["instructions"].lower() or \
               "vulnerab" in sentinel["instructions"].lower()

    def test_agent_dicts_are_agent_compatible(self):
        """Returned dicts have keys compatible with Agent(**cfg)."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf"]}
        )
        required_keys = {"name", "model", "instructions", "description"}
        for member in members:
            assert required_keys.issubset(member.keys()), (
                f"Missing keys: {required_keys - member.keys()}"
            )
            assert "team_mode" not in member  # AC-07: not in agent dict

    def test_team_param_accepted(self):
        """team parameter is accepted (Agno F7 compatibility)."""
        members = resolve_design_members(
            "mock_team", {"project_tags": ["ebpf"]}
        )
        assert len(members) == 2


class TestCreateMemberFactory:
    def test_returns_callable(self):
        factory = create_member_factory()
        assert callable(factory)

    def test_factory_returns_members(self):
        factory = create_member_factory()
        members = factory(None, {"project_tags": ["ebpf"]})
        assert len(members) == 2
        assert members[0]["name"] == "Architect"
        assert members[1]["name"] == "Probe"

    def test_factory_with_custom_mappings(self):
        custom = [
            SpecialistMapping(tag="ml", name="Learner", role_description="ML Expert"),
        ]
        factory = create_member_factory(tag_mappings=custom)
        members = factory(None, {"project_tags": ["ml"]})
        assert len(members) == 2
        assert members[1]["name"] == "Learner"

    def test_factory_ignores_default_tags_with_custom(self):
        """Custom mappings replace defaults entirely."""
        custom = [
            SpecialistMapping(tag="ml", name="Learner", role_description="ML Expert"),
        ]
        factory = create_member_factory(tag_mappings=custom)
        members = factory(None, {"project_tags": ["ebpf"]})
        assert len(members) == 1  # only Architect, ebpf not in custom


class TestResolveDesignMembersCustomMappings:
    def test_custom_tag_mapping(self):
        custom = [
            SpecialistMapping(
                tag="ml",
                name="Learner",
                role_description="Machine Learning Expert",
                extra_instructions=["Focus on model training pipelines."],
            ),
        ]
        members = resolve_design_members(
            None, {"project_tags": ["ml"]}, tag_mappings=custom
        )
        assert len(members) == 2
        assert members[1]["name"] == "Learner"
        assert members[1]["role"] == "Machine Learning Expert"

    def test_empty_custom_mappings(self):
        """Empty custom mappings → only Architect, all tags unknown."""
        members = resolve_design_members(
            None, {"project_tags": ["ebpf"]}, tag_mappings=[]
        )
        assert len(members) == 1  # only Architect


class TestListAvailableTags:
    def test_default_tags(self):
        tags = list_available_tags()
        assert tags == ["ebpf", "frontend", "data", "security"]

    def test_custom_tags(self):
        custom = [
            SpecialistMapping(tag="ml", name="Learner", role_description="ML"),
            SpecialistMapping(tag="infra", name="Ops", role_description="Infra"),
        ]
        tags = list_available_tags(tag_mappings=custom)
        assert tags == ["ml", "infra"]
