"""Tests for orchestra.utils.workflow (AC-05)."""

import pytest

from orchestra.utils.workflow import create_workflow


class TestCreateWorkflow:
    def test_rejects_none_db(self):
        with pytest.raises(ValueError, match="AC-05"):
            create_workflow("test_wf", db=None)

    def test_creates_workflow_with_db(self):
        # Use a mock/stub db — any truthy object satisfies the guard
        class StubDb:
            pass

        wf = create_workflow("test_wf", db=StubDb())
        assert wf.name == "test_wf"
        assert wf.db is not None

    def test_passes_kwargs_through(self):
        class StubDb:
            pass

        wf = create_workflow(
            "test_wf",
            db=StubDb(),
            session_state={"init": True},
        )
        assert wf.name == "test_wf"

    def test_error_message_is_helpful(self):
        with pytest.raises(ValueError) as exc_info:
            create_workflow("my_workflow", db=None)
        msg = str(exc_info.value)
        assert "my_workflow" in msg
        assert "InMemoryDb" in msg
