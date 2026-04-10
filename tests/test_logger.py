"""Tests for P0-10: Multi-format logging system.

Covers all four output files, redaction integration, and failure paths:
- session.log format and content
- events.ndjson structured output
- tools.ndjson tool invocation logging
- errors.log error-level filtering + traceback inclusion
- Secret redaction in all output channels
- Edge cases: closed logger, context manager, lazy init
- Error path: exceptions with tracebacks, failed tool calls
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra.observability.logger import (
    SessionLogger,
    close_logger,
    get_logger,
)


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def logger(log_dir: Path) -> SessionLogger:
    lg = SessionLogger(
        agent_name="test-agent",
        session_id="sess-001",
        log_root=log_dir,
        timestamp="20260410T120000Z",
    )
    yield lg
    lg.close()


def _session_dir(log_dir: Path) -> Path:
    return log_dir / "test-agent" / "20260410T120000Z"


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").strip().splitlines()


def _read_ndjson(path: Path) -> list[dict]:
    return [json.loads(line) for line in _read_lines(path)]


# ---------------------------------------------------------------------------
# Directory and file creation
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_no_dir_before_write(self, logger: SessionLogger, log_dir: Path) -> None:
        # Logger created but no log written yet — directory should not exist
        assert not _session_dir(log_dir).exists()

    def test_dir_created_on_first_write(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("startup")
        session_dir = _session_dir(log_dir)
        assert session_dir.exists()
        assert (session_dir / "session.log").exists()
        assert (session_dir / "events.ndjson").exists()
        assert (session_dir / "tools.ndjson").exists()
        assert (session_dir / "errors.log").exists()


# ---------------------------------------------------------------------------
# session.log format
# ---------------------------------------------------------------------------


class TestSessionLog:
    def test_info_line_format(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("workflow_started", step="plan")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert len(lines) == 1
        line = lines[0]
        # Format: [TIMESTAMP] LEVEL EVENT key=value
        assert "] INFO workflow_started step=plan" in line
        assert line.startswith("[")

    def test_multiple_kv_pairs(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("step_done", step="review", duration="3.2s")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert "step=review" in lines[0]
        assert "duration=3.2s" in lines[0]

    def test_debug_level(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.debug("detail", foo="bar")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert "DEBUG" in lines[0]

    def test_warning_level(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.warning("slow_response", latency_ms=500)
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert "WARNING" in lines[0]

    def test_critical_level(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.critical("system_failure", reason="OOM")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert "CRITICAL" in lines[0]

    def test_multiline_session_log(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("event1")
        logger.info("event2")
        logger.warning("event3")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert len(lines) >= 3


# ---------------------------------------------------------------------------
# events.ndjson structured output
# ---------------------------------------------------------------------------


class TestEventsNdjson:
    def test_event_record_structure(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("workflow_started", step="plan")
        records = _read_ndjson(_session_dir(log_dir) / "events.ndjson")
        assert len(records) == 1
        r = records[0]
        assert r["level"] == "INFO"
        assert r["event"] == "workflow_started"
        assert r["agent"] == "test-agent"
        assert r["session_id"] == "sess-001"
        assert r["data"]["step"] == "plan"
        assert "ts" in r

    def test_event_without_extra(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("bare_event")
        records = _read_ndjson(_session_dir(log_dir) / "events.ndjson")
        assert "data" not in records[0]

    def test_multiple_events(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("event1")
        logger.warning("event2")
        records = _read_ndjson(_session_dir(log_dir) / "events.ndjson")
        assert len(records) == 2
        assert records[0]["event"] == "event1"
        assert records[1]["event"] == "event2"

    def test_valid_json_per_line(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("a")
        logger.info("b")
        lines = _read_lines(_session_dir(log_dir) / "events.ndjson")
        for line in lines:
            json.loads(line)  # should not raise


# ---------------------------------------------------------------------------
# tools.ndjson
# ---------------------------------------------------------------------------


class TestToolsNdjson:
    def test_tool_success(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.tool("shell", {"cmd": "git status"}, {"stdout": "clean"})
        records = _read_ndjson(_session_dir(log_dir) / "tools.ndjson")
        assert len(records) == 1
        r = records[0]
        assert r["tool"] == "shell"
        assert r["success"] is True
        assert r["input"]["cmd"] == "git status"
        assert r["output"]["stdout"] == "clean"

    def test_tool_with_duration(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.tool("shell", {"cmd": "ls"}, duration_ms=12.5)
        records = _read_ndjson(_session_dir(log_dir) / "tools.ndjson")
        assert records[0]["duration_ms"] == 12.5

    def test_tool_failure(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.tool("shell", {"cmd": "bad"}, success=False, error="exit code 1")
        records = _read_ndjson(_session_dir(log_dir) / "tools.ndjson")
        assert records[0]["success"] is False
        assert records[0]["error"] == "exit code 1"

    def test_tool_failure_in_errors_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.tool("shell", {"cmd": "bad"}, success=False, error="exit code 1")
        errors = _read_lines(_session_dir(log_dir) / "errors.log")
        assert any("exit code 1" in line for line in errors)

    def test_tool_also_in_session_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.tool("shell", {"cmd": "ls"})
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert any("tool_invocation" in line and "shell" in line for line in lines)

    def test_tool_also_in_events_ndjson(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.tool("shell", {"cmd": "ls"})
        records = _read_ndjson(_session_dir(log_dir) / "events.ndjson")
        tool_events = [r for r in records if r["event"] == "tool_invocation"]
        assert len(tool_events) == 1
        assert tool_events[0]["data"]["tool"] == "shell"


# ---------------------------------------------------------------------------
# errors.log
# ---------------------------------------------------------------------------


class TestErrorsLog:
    def test_error_level_writes_to_errors_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.log("ERROR", "api_failed", reason="timeout")
        errors = _read_lines(_session_dir(log_dir) / "errors.log")
        assert len(errors) >= 1
        assert "api_failed" in errors[0]

    def test_critical_writes_to_errors_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.critical("system_crash")
        errors = _read_lines(_session_dir(log_dir) / "errors.log")
        assert any("system_crash" in line for line in errors)

    def test_info_does_not_write_to_errors_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("normal_event")
        errors_path = _session_dir(log_dir) / "errors.log"
        content = errors_path.read_text(encoding="utf-8").strip()
        assert content == ""

    def test_error_with_exception(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        try:
            raise ValueError("test error message")
        except ValueError as e:
            logger.error("handler_failed", error=e)
        errors = _read_lines(_session_dir(log_dir) / "errors.log")
        joined = "\n".join(errors)
        assert "ValueError" in joined
        assert "test error message" in joined

    def test_error_traceback_in_errors_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            logger.error("crash", error=e)
        errors = "\n".join(_read_lines(_session_dir(log_dir) / "errors.log"))
        assert "Traceback" in errors
        assert "RuntimeError: boom" in errors


# ---------------------------------------------------------------------------
# Secret redaction integration
# ---------------------------------------------------------------------------


class TestRedactionIntegration:
    def test_session_log_redacts_secrets(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info(
            "auth_configured",
            key="sk-ant-abcdefghijklmnopqrstuvwxyz1234567890",
        )
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        joined = "\n".join(lines)
        assert "abcdefghijklmnopqrstuvwxyz" not in joined
        assert "[REDACTED_ANTHROPIC_KEY]" in joined

    def test_events_ndjson_redacts_secrets(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info(
            "config_loaded",
            api_key="ghp_abcdefghijklmnopqrstuvwxyz1234",
        )
        content = (_session_dir(log_dir) / "events.ndjson").read_text()
        assert "abcdefghijklmnopqrstuvwxyz" not in content
        assert "REDACTED" in content

    def test_tools_ndjson_redacts_secrets(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.tool(
            "shell",
            {"cmd": "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret123456789012345678'"},
        )
        content = (_session_dir(log_dir) / "tools.ndjson").read_text()
        assert "eyJhbGci" not in content
        assert "Bearer [REDACTED]" in content

    def test_errors_log_redacts_secrets(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        try:
            raise ConnectionError(
                "Failed connecting with key sk-ant-abcdefghijklmnopqrstuvwxyz1234567890"
            )
        except ConnectionError as e:
            logger.error("connection_failed", error=e)
        content = (_session_dir(log_dir) / "errors.log").read_text()
        assert "abcdefghijklmnopqrstuvwxyz" not in content
        assert "[REDACTED_ANTHROPIC_KEY]" in content

    def test_url_credentials_redacted_in_session_log(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("connecting", url="https://admin:s3cret@db.example.com/mydb")
        content = (_session_dir(log_dir) / "session.log").read_text()
        assert "s3cret" not in content
        assert "[REDACTED]" in content

    def test_env_var_redacted(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("env", value="ANTHROPIC_API_KEY=sk-ant-realkey123456789012345678901234")
        content = (_session_dir(log_dir) / "session.log").read_text()
        assert "realkey" not in content


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPathTraversalPrevention:
    """Verify agent_name / session_id cannot escape log_root."""

    def test_rejects_dotdot_in_agent_name(self, log_dir: Path) -> None:
        with pytest.raises(ValueError, match="Path separators"):
            SessionLogger(
                agent_name="../escape",
                session_id="sess",
                log_root=log_dir,
                timestamp="ts",
            )

    def test_rejects_slash_in_agent_name(self, log_dir: Path) -> None:
        with pytest.raises(ValueError, match="Path separators"):
            SessionLogger(
                agent_name="foo/bar",
                session_id="sess",
                log_root=log_dir,
                timestamp="ts",
            )

    def test_rejects_dotdot_in_session_id(self, log_dir: Path) -> None:
        with pytest.raises(ValueError, match="Path separators"):
            SessionLogger(
                agent_name="agent",
                session_id="../../etc",
                log_root=log_dir,
                timestamp="ts",
            )

    def test_rejects_empty_agent_name(self, log_dir: Path) -> None:
        with pytest.raises(ValueError):
            SessionLogger(
                agent_name="",
                session_id="sess",
                log_root=log_dir,
                timestamp="ts",
            )

    def test_accepts_safe_names(self, log_dir: Path) -> None:
        lg = SessionLogger(
            agent_name="my-agent_v2.1",
            session_id="sess-001",
            log_root=log_dir,
            timestamp="20260410T120000Z",
        )
        assert lg.log_dir.resolve().is_relative_to(log_dir.resolve())
        lg.close()


class TestEdgeCases:
    def test_write_after_close_is_noop(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.info("before_close")
        logger.close()
        logger.info("after_close")  # should not raise
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert len(lines) == 1
        assert "before_close" in lines[0]

    def test_double_close_is_safe(self, logger: SessionLogger) -> None:
        logger.info("test")
        logger.close()
        logger.close()  # should not raise

    def test_context_manager(self, log_dir: Path) -> None:
        with SessionLogger(
            agent_name="ctx-agent",
            session_id="sess-ctx",
            log_root=log_dir,
            timestamp="20260410T000000Z",
        ) as lg:
            lg.info("inside_context")
        # After context exit, logger should be closed
        assert lg._closed

    def test_properties(self, logger: SessionLogger, log_dir: Path) -> None:
        assert logger.agent_name == "test-agent"
        assert logger.session_id == "sess-001"
        assert logger.log_dir == _session_dir(log_dir)

    def test_unicode_content(self, logger: SessionLogger, log_dir: Path) -> None:
        logger.info("状态更新", message="日志系统正常运行 🚀")
        lines = _read_lines(_session_dir(log_dir) / "session.log")
        assert "状态更新" in lines[0]


# ---------------------------------------------------------------------------
# get_logger / close_logger registry
# ---------------------------------------------------------------------------


class TestLoggerRegistry:
    def test_get_logger_returns_same_instance(self, log_dir: Path) -> None:
        lg1 = get_logger("agent-a", "sess-1", log_root=log_dir)
        lg2 = get_logger("agent-a", "sess-1", log_root=log_dir)
        assert lg1 is lg2
        close_logger("agent-a", "sess-1")

    def test_different_sessions_different_loggers(self, log_dir: Path) -> None:
        lg1 = get_logger("agent-a", "sess-1", log_root=log_dir)
        lg2 = get_logger("agent-a", "sess-2", log_root=log_dir)
        assert lg1 is not lg2
        close_logger("agent-a", "sess-1")
        close_logger("agent-a", "sess-2")

    def test_close_logger_removes_from_registry(self, log_dir: Path) -> None:
        lg1 = get_logger("agent-b", "sess-1", log_root=log_dir)
        close_logger("agent-b", "sess-1")
        lg2 = get_logger("agent-b", "sess-1", log_root=log_dir)
        assert lg1 is not lg2
        close_logger("agent-b", "sess-1")

    def test_close_nonexistent_is_safe(self) -> None:
        close_logger("no-such-agent", "no-such-session")  # should not raise

    def test_get_logger_after_direct_close_returns_new_instance(
        self, log_dir: Path
    ) -> None:
        """Closing a logger directly (not via close_logger) must not leave
        a dead instance in the registry that silently drops writes."""
        lg1 = get_logger("agent-c", "sess-1", log_root=log_dir)
        lg1.info("before")
        lg1.close()  # direct close, not close_logger
        lg2 = get_logger("agent-c", "sess-1", log_root=log_dir)
        assert lg2 is not lg1  # must be a fresh instance
        assert not lg2._closed
        lg2.info("after")  # must actually write
        close_logger("agent-c", "sess-1")


# ---------------------------------------------------------------------------
# Error path: traceback content redacted in all files
# ---------------------------------------------------------------------------


class TestErrorPathRedaction:
    """Verify secrets in tracebacks and exception messages are redacted
    across all output channels — the key requirement from Challenger."""

    def test_traceback_with_bearer_token(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        try:
            raise ConnectionError(
                "Auth failed with Bearer eyJhbGciOiJIUzI1NiJ9.payload123456789012345678"
            )
        except ConnectionError as e:
            logger.error("auth_error", error=e)

        session_dir = _session_dir(log_dir)

        # Check all files that received the error
        for filename in ("session.log", "events.ndjson", "errors.log"):
            content = (session_dir / filename).read_text()
            assert "eyJhbGci" not in content, f"Bearer token leaked in {filename}"
            assert "Bearer [REDACTED]" in content, f"Missing redaction in {filename}"

    def test_traceback_with_github_token(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        try:
            raise RuntimeError(
                "API call with ghp_abcdefghijklmnopqrstuvwxyz1234 failed"
            )
        except RuntimeError as e:
            logger.error("github_error", error=e)

        content = (_session_dir(log_dir) / "errors.log").read_text()
        assert "abcdefghijklmnopqrstuvwxyz" not in content
        assert "[REDACTED_GITHUB_TOKEN]" in content

    def test_failed_tool_with_secret_in_error(
        self, logger: SessionLogger, log_dir: Path
    ) -> None:
        logger.tool(
            "api_call",
            {"url": "https://api.example.com"},
            success=False,
            error="auth failed: key=sk-ant-abcdefghijklmnopqrstuvwxyz1234567890",
        )
        session_dir = _session_dir(log_dir)
        for filename in ("session.log", "tools.ndjson", "errors.log"):
            content = (session_dir / filename).read_text()
            assert "abcdefghijklmnopqrstuvwxyz" not in content, (
                f"Secret leaked in {filename}"
            )
