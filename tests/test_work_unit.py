"""Tests for WorkUnit data model."""

import pytest

from orchestra.models.work_unit import WorkUnit


def _full_wu(**overrides) -> WorkUnit:
    """Create a valid WorkUnit with all required fields, with optional overrides."""
    defaults = {
        "id": "wu-001",
        "title": "Test unit",
        "description": "Test description",
        "dod": ["Tests pass"],
        "file_scope": ["src/test.py"],
        "estimated_complexity": "M",
    }
    defaults.update(overrides)
    return WorkUnit(**defaults)


class TestWorkUnitModel:
    """WorkUnit Pydantic model validation."""

    def test_all_required_fields(self):
        wu = _full_wu()
        assert wu.id == "wu-001"
        assert wu.title == "Test unit"
        assert wu.description == "Test description"
        assert wu.dod == ["Tests pass"]
        assert wu.file_scope == ["src/test.py"]
        assert wu.dependencies == []
        assert wu.estimated_complexity == "M"
        assert wu.assigned_model is None

    def test_full_valid(self):
        wu = WorkUnit(
            id="wu-002",
            title="Add logging middleware",
            description="Wrap all request handlers",
            dod=["Tests pass", "Coverage > 80%"],
            file_scope=["src/middleware/*.py", "tests/test_middleware.py"],
            dependencies=["wu-001"],
            estimated_complexity="L",
            assigned_model="claude-opus-4-6",
        )
        assert wu.estimated_complexity == "L"
        assert wu.dependencies == ["wu-001"]
        assert wu.assigned_model == "claude-opus-4-6"

    def test_invalid_complexity(self):
        with pytest.raises(Exception):
            _full_wu(estimated_complexity="XL")

    def test_missing_required_id(self):
        with pytest.raises(Exception):
            WorkUnit(
                title="No ID",
                description="desc",
                dod=["x"],
                file_scope=["y"],
                estimated_complexity="S",
            )  # type: ignore[call-arg]

    def test_missing_required_title(self):
        with pytest.raises(Exception):
            WorkUnit(
                id="wu-no-title",
                description="desc",
                dod=["x"],
                file_scope=["y"],
                estimated_complexity="S",
            )  # type: ignore[call-arg]

    def test_missing_required_description(self):
        with pytest.raises(Exception):
            WorkUnit(id="wu-x", title="X", dod=["x"], file_scope=["y"], estimated_complexity="S")  # type: ignore[call-arg]

    def test_missing_required_dod(self):
        with pytest.raises(Exception):
            WorkUnit(id="wu-x", title="X", description="d", file_scope=["y"], estimated_complexity="S")  # type: ignore[call-arg]

    def test_missing_required_file_scope(self):
        with pytest.raises(Exception):
            WorkUnit(id="wu-x", title="X", description="d", dod=["x"], estimated_complexity="S")  # type: ignore[call-arg]

    def test_missing_required_complexity(self):
        with pytest.raises(Exception):
            WorkUnit(id="wu-x", title="X", description="d", dod=["x"], file_scope=["y"])  # type: ignore[call-arg]

    def test_serialization_roundtrip(self):
        wu = _full_wu(
            id="wu-rt",
            title="Roundtrip test",
            dod=["Check 1"],
            file_scope=["src/*.py"],
            dependencies=["wu-001"],
            estimated_complexity="S",
        )
        data = wu.model_dump()
        restored = WorkUnit(**data)
        assert restored == wu

    def test_json_roundtrip(self):
        wu = _full_wu(id="wu-json", title="JSON test", dependencies=["wu-001"])
        json_str = wu.model_dump_json()
        restored = WorkUnit.model_validate_json(json_str)
        assert restored == wu
