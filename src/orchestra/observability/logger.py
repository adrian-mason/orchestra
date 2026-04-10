"""Multi-format logging system for Orchestra agent sessions.

P0-10: Implements 4-file logging per agent session as specified in DESIGN.md §8.3:

    .orchestra/logs/{agent-name}/{session-timestamp}/
    ├── session.log      # Human-readable [TIMESTAMP] LEVEL EVENT key=value
    ├── events.ndjson    # Machine-parsable NDJSON (all structured events)
    ├── tools.ndjson     # Tool invocation/result logs
    └── errors.log       # Error-level entries with tracebacks

All output is passed through the sanitizer (P0-11) before writing to prevent
credential leakage. The sanitizer runs synchronously in the log-write path —
secrets are redacted before bytes hit disk, not after.
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestra.observability.sanitizer import sanitize


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_ROOT = ".orchestra/logs"
_SESSION_LOG = "session.log"
_EVENTS_NDJSON = "events.ndjson"
_TOOLS_NDJSON = "tools.ndjson"

# Safe path segment pattern: alphanumeric, hyphens, underscores, dots (no slashes, no ..)
_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_ERRORS_LOG = "errors.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _format_session_line(
    timestamp: str,
    level: str,
    event: str,
    **kv: Any,
) -> str:
    """Format a human-readable session.log line.

    Format: [TIMESTAMP] LEVEL EVENT key=value key2=value2
    """
    parts = [f"[{timestamp}]", level, event]
    for k, v in kv.items():
        parts.append(f"{k}={v}")
    return " ".join(parts)


def _build_event_record(
    timestamp: str,
    level: str,
    event: str,
    *,
    agent: str,
    session_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured event dict for NDJSON output."""
    record: dict[str, Any] = {
        "ts": timestamp,
        "level": level,
        "event": event,
        "agent": agent,
        "session_id": session_id,
    }
    if extra:
        record["data"] = extra
    return record


# ---------------------------------------------------------------------------
# SessionLogger
# ---------------------------------------------------------------------------


class SessionLogger:
    """Per-session multi-format logger.

    Creates the session log directory on first write (lazy) and manages
    four output files. All text is sanitized before writing.

    Usage::

        logger = SessionLogger(agent_name="reviewer", session_id="abc123")
        logger.info("workflow_started", step="plan")
        logger.tool("shell", {"cmd": "git diff"}, {"stdout": "..."})
        logger.error("api_call_failed", error=exc, traceback=tb_str)
        logger.close()
    """

    def __init__(
        self,
        *,
        agent_name: str,
        session_id: str,
        log_root: str | Path = _DEFAULT_LOG_ROOT,
        timestamp: str | None = None,
    ) -> None:
        # Validate path segments to prevent directory traversal
        for name, value in [("agent_name", agent_name), ("session_id", session_id)]:
            if not _SAFE_SEGMENT.match(value):
                raise ValueError(
                    f"{name} must contain only alphanumeric characters, "
                    f"hyphens, underscores, or dots (got {value!r}). "
                    f"Path separators and '..' are not allowed."
                )

        self._agent_name = agent_name
        self._session_id = session_id

        # Session directory: {log_root}/{agent_name}/{session_timestamp}/
        session_ts = timestamp or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        self._log_dir = Path(log_root) / agent_name / session_ts
        self._initialized = False

        # File handles — opened lazily on first write
        self._session_fh: Any = None
        self._events_fh: Any = None
        self._tools_fh: Any = None
        self._errors_fh: Any = None
        self._closed = False

    # -- Properties --

    @property
    def log_dir(self) -> Path:
        """Return the session log directory path."""
        return self._log_dir

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def session_id(self) -> str:
        return self._session_id

    # -- Lifecycle --

    def _ensure_initialized(self) -> None:
        """Create log directory and open file handles on first write."""
        if self._initialized:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        # Open all four files in append mode
        self._session_fh = open(self._log_dir / _SESSION_LOG, "a", encoding="utf-8")
        self._events_fh = open(self._log_dir / _EVENTS_NDJSON, "a", encoding="utf-8")
        self._tools_fh = open(self._log_dir / _TOOLS_NDJSON, "a", encoding="utf-8")
        self._errors_fh = open(self._log_dir / _ERRORS_LOG, "a", encoding="utf-8")
        self._initialized = True

    def close(self) -> None:
        """Flush and close all file handles."""
        if self._closed:
            return
        self._closed = True
        for fh in (self._session_fh, self._events_fh, self._tools_fh, self._errors_fh):
            if fh is not None:
                fh.flush()
                fh.close()

    def __enter__(self) -> SessionLogger:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # -- Internal write helpers --

    def _write_session(self, line: str) -> None:
        """Write a sanitized line to session.log."""
        self._ensure_initialized()
        self._session_fh.write(sanitize(line) + "\n")
        self._session_fh.flush()

    def _write_event(self, record: dict[str, Any]) -> None:
        """Write a sanitized NDJSON line to events.ndjson."""
        self._ensure_initialized()
        raw = json.dumps(record, ensure_ascii=False, default=str)
        self._events_fh.write(sanitize(raw) + "\n")
        self._events_fh.flush()

    def _write_tool(self, record: dict[str, Any]) -> None:
        """Write a sanitized NDJSON line to tools.ndjson."""
        self._ensure_initialized()
        raw = json.dumps(record, ensure_ascii=False, default=str)
        self._tools_fh.write(sanitize(raw) + "\n")
        self._tools_fh.flush()

    def _write_error(self, text: str) -> None:
        """Write sanitized error text to errors.log."""
        self._ensure_initialized()
        self._errors_fh.write(sanitize(text) + "\n")
        self._errors_fh.flush()

    # -- Public logging API --

    def log(
        self,
        level: str,
        event: str,
        *,
        extra: dict[str, Any] | None = None,
        **kv: Any,
    ) -> None:
        """Log a structured event at the given level.

        Writes to both session.log (human-readable) and events.ndjson
        (machine-parsable). If level is ERROR or CRITICAL, also writes
        to errors.log.
        """
        if self._closed:
            return
        level = level.upper()
        ts = _now_iso()

        # session.log — human-readable
        merged_kv = {**(extra or {}), **kv}
        line = _format_session_line(ts, level, event, **merged_kv)
        self._write_session(line)

        # events.ndjson — structured
        record = _build_event_record(
            ts,
            level,
            event,
            agent=self._agent_name,
            session_id=self._session_id,
            extra=merged_kv if merged_kv else None,
        )
        self._write_event(record)

        # errors.log — error-level entries
        if level in ("ERROR", "CRITICAL"):
            error_text = line
            if "traceback" in merged_kv:
                error_text += "\n" + str(merged_kv["traceback"])
            self._write_error(error_text)

    def debug(self, event: str, **kv: Any) -> None:
        self.log("DEBUG", event, **kv)

    def info(self, event: str, **kv: Any) -> None:
        self.log("INFO", event, **kv)

    def warning(self, event: str, **kv: Any) -> None:
        self.log("WARNING", event, **kv)

    def error(
        self,
        event: str,
        *,
        error: BaseException | None = None,
        **kv: Any,
    ) -> None:
        """Log an error event with optional exception details."""
        if error is not None:
            kv["error_type"] = type(error).__name__
            kv["error_msg"] = str(error)
            kv["traceback"] = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        self.log("ERROR", event, **kv)

    def critical(self, event: str, **kv: Any) -> None:
        self.log("CRITICAL", event, **kv)

    def tool(
        self,
        tool_name: str,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        *,
        duration_ms: float | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Log a tool invocation to tools.ndjson and session.log.

        Also writes to events.ndjson as a tool_invocation event.
        """
        if self._closed:
            return
        ts = _now_iso()

        tool_record: dict[str, Any] = {
            "ts": ts,
            "tool": tool_name,
            "agent": self._agent_name,
            "session_id": self._session_id,
            "success": success,
        }
        if input_data is not None:
            tool_record["input"] = input_data
        if output_data is not None:
            tool_record["output"] = output_data
        if duration_ms is not None:
            tool_record["duration_ms"] = duration_ms
        if error is not None:
            tool_record["error"] = error

        # tools.ndjson
        self._write_tool(tool_record)

        # session.log — summary line
        level = "INFO" if success else "ERROR"
        kv: dict[str, Any] = {"tool": tool_name}
        if duration_ms is not None:
            kv["duration_ms"] = f"{duration_ms:.1f}"
        if error:
            kv["error"] = error
        line = _format_session_line(ts, level, "tool_invocation", **kv)
        self._write_session(line)

        # events.ndjson
        event_record = _build_event_record(
            ts,
            level,
            "tool_invocation",
            agent=self._agent_name,
            session_id=self._session_id,
            extra={"tool": tool_name, "success": success},
        )
        self._write_event(event_record)

        # errors.log for failed tools
        if not success:
            error_parts = [line]
            if error:
                error_parts.append(f"  Error: {error}")
            self._write_error("\n".join(error_parts))


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_active_loggers: dict[str, SessionLogger] = {}


def get_logger(
    agent_name: str,
    session_id: str,
    *,
    log_root: str | Path = _DEFAULT_LOG_ROOT,
) -> SessionLogger:
    """Get or create a SessionLogger for the given agent + session.

    Returns the same instance if called again with the same session_id.
    """
    key = f"{agent_name}:{session_id}"
    existing = _active_loggers.get(key)
    if existing is None or existing._closed:
        _active_loggers[key] = SessionLogger(
            agent_name=agent_name,
            session_id=session_id,
            log_root=log_root,
        )
    return _active_loggers[key]


def close_logger(agent_name: str, session_id: str) -> None:
    """Close and remove a logger from the active registry."""
    key = f"{agent_name}:{session_id}"
    logger = _active_loggers.pop(key, None)
    if logger is not None:
        logger.close()
