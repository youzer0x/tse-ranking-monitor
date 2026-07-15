"""Low-overhead JSONL telemetry for Claude routine sessions.

The logger intentionally stores metadata and size proxies, never tool inputs,
tool responses, prompts, or assistant text.  This keeps credentials and large
research payloads out of telemetry while still making token-amplifying fan-out
and retries measurable.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path


TELEMETRY_SCHEMA_VERSION = 1
_SESSION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_TOKEN_NAMES = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_size(value):
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _safe_text(value, limit=500):
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] or None


def _usage_from_hook(payload):
    candidates = [payload.get("usage")]
    response = payload.get("tool_response")
    if isinstance(response, dict):
        candidates.append(response.get("usage"))
    for usage in candidates:
        if not isinstance(usage, dict):
            continue
        values = {
            name: usage.get(name)
            for name in _TOKEN_NAMES
            if isinstance(usage.get(name), int) and not isinstance(usage.get(name), bool)
        }
        if values:
            return {"source": "hook_payload", **values}
    return {"source": "unavailable"}


def event_from_hook(payload, environ=None):
    """Convert Claude hook JSON to a bounded, secret-free telemetry event."""
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be an object")
    environ = environ or os.environ
    hook = str(payload.get("hook_event_name") or "unknown")
    mapping = {
        "SessionStart": ("session_start", "started"),
        "SessionEnd": ("session_end", "completed"),
        "Stop": ("session_stop", "completed"),
        "StopFailure": ("session_failure", "failed"),
        "SubagentStart": ("subagent_start", "started"),
        "SubagentStop": ("subagent_stop", "completed"),
        "PostToolUse": ("tool_end", "completed"),
        "PostToolUseFailure": ("tool_end", "failed"),
    }
    event_name, status = mapping.get(hook, ("hook_event", "observed"))
    effort = payload.get("effort")
    if isinstance(effort, dict):
        effort = effort.get("level")
    effort = effort or environ.get("CLAUDE_EFFORT")

    event = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "timestamp": utc_now(),
        "event": event_name,
        "hook_event": hook,
        "status": status,
        "run_id": _safe_text(payload.get("session_id"), 128),
        "session_date": _safe_text(environ.get("TSE_SESSION"), 10),
        "model": _safe_text(payload.get("model"), 128),
        "effort": _safe_text(effort, 32),
        "agent_id": _safe_text(payload.get("agent_id"), 128),
        "agent_type": _safe_text(payload.get("agent_type"), 128),
        "tool_name": _safe_text(payload.get("tool_name"), 128),
        "tool_use_id": _safe_text(payload.get("tool_use_id"), 128),
        "error_type": _safe_text(payload.get("error"), 128)
        if hook == "StopFailure" else ("tool_failure" if hook == "PostToolUseFailure" else None),
        "error_chars": _json_size(payload.get("error_details") or payload.get("error")),
        "input_chars": _json_size(payload.get("tool_input")),
        "output_chars": _json_size(payload.get("tool_response")),
        "assistant_chars": len(str(payload.get("last_assistant_message") or "")),
        "tokens": _usage_from_hook(payload),
    }
    return {key: value for key, value in event.items() if value is not None}


class TelemetryWriter:
    """Append one compact JSON object per line below ``<root>/.work``."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def event_path(self, session_date=None):
        if session_date and _SESSION_DATE_RE.fullmatch(str(session_date)):
            return self.root / ".work" / str(session_date) / "telemetry" / "events.jsonl"
        return self.root / ".work" / "_runtime" / "events.jsonl"

    def append(self, event, session_date=None):
        if not isinstance(event, dict):
            raise ValueError("telemetry event must be an object")
        payload = dict(event)
        payload.setdefault("schema_version", TELEMETRY_SCHEMA_VERSION)
        payload.setdefault("timestamp", utc_now())
        selected_session = session_date or payload.get("session_date")
        path = self.event_path(selected_session)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        # Windows does not guarantee that concurrent O_APPEND handles share an
        # atomic seek/write window.  An adjacent lock directory gives us a
        # portable inter-process mutex without third-party dependencies.
        lock = path.with_name(path.name + ".append-lock")
        deadline = time.monotonic() + 2.0
        while True:
            try:
                lock.mkdir()
                break
            except (FileExistsError, PermissionError):
                # Windows may surface a concurrent create/remove race as
                # ERROR_ACCESS_DENIED instead of ERROR_ALREADY_EXISTS.
                if time.monotonic() >= deadline:
                    raise TimeoutError("telemetry append lock timed out")
                time.sleep(0.005)
        try:
            with path.open("ab", buffering=0) as stream:
                written = stream.write(encoded)
                if written != len(encoded):
                    raise OSError("partial telemetry append")
        finally:
            try:
                lock.rmdir()
            except FileNotFoundError:
                pass
        return path

    def _stage_state_path(self, session_date, stage):
        safe_stage = _SAFE_COMPONENT_RE.sub("-", str(stage)).strip("-.") or "unknown"
        return (
            self.root / ".work" / str(session_date) / "telemetry"
            / (".%s.stage.json" % safe_stage)
        )

    def record_stage(self, session_date, stage, phase, status=None):
        """Record a deterministic stage start/end and end-to-end duration."""
        if not _SESSION_DATE_RE.fullmatch(str(session_date)):
            raise ValueError("session_date must be YYYY-MM-DD")
        if phase not in ("start", "end"):
            raise ValueError("phase must be start or end")
        state_path = self._stage_state_path(session_date, stage)
        event = {
            "event": "stage_%s" % phase,
            "session_date": str(session_date),
            "stage": str(stage),
            "status": status or ("started" if phase == "start" else "completed"),
        }
        if phase == "start":
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"started_ns": time.time_ns()}), encoding="utf-8"
            )
        else:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                started_ns = int(state["started_ns"])
                event["duration_ms"] = max(0, (time.time_ns() - started_ns) // 1_000_000)
            except (OSError, ValueError, KeyError, TypeError):
                event["duration_source"] = "unavailable"
            try:
                state_path.unlink()
            except FileNotFoundError:
                pass
        return self.append(event, session_date)


def compact_line(event):
    """Return the one-line, low-token hook diagnostic shown in transcripts."""
    parts = ["[routine-metrics]", str(event.get("event", "event"))]
    for name in ("session_date", "agent_type", "tool_name", "status", "error_type"):
        if event.get(name):
            parts.append("%s=%s" % (name, event[name]))
    tokens = event.get("tokens") or {}
    parts.append("tokens=%s" % ("actual" if tokens.get("source") == "hook_payload" else "unavailable"))
    return " ".join(parts)


def summarize_events(events, session_date=None):
    """Aggregate hook/stage events into a small, publish-safe metrics object."""
    selected = [
        event for event in events
        if not session_date or event.get("session_date") in (None, session_date)
    ]
    subagent_starts = sum(event.get("event") == "subagent_start" for event in selected)
    subagent_stops = sum(event.get("event") == "subagent_stop" for event in selected)
    tool_events = [event for event in selected if event.get("event") == "tool_end"]
    failures = [event for event in selected if event.get("status") == "failed"]
    known_usage = [
        event.get("tokens", {}) for event in selected
        if (event.get("tokens") or {}).get("source") == "hook_payload"
    ]
    token_totals = {
        name: sum(usage.get(name, 0) for usage in known_usage)
        for name in _TOKEN_NAMES
    }
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "session_date": session_date,
        "events": len(selected),
        "subagents": {"started": subagent_starts, "completed": subagent_stops},
        "tools": {
            "completed": sum(event.get("status") == "completed" for event in tool_events),
            "failed": sum(event.get("status") == "failed" for event in tool_events),
        },
        "failures": len(failures),
        "tokens": {
            "source": "hook_payload" if known_usage else "unavailable",
            **token_totals,
        },
        "stages_ms": {
            str(event.get("stage")): event["duration_ms"]
            for event in selected
            if event.get("event") == "stage_end" and "duration_ms" in event
        },
    }
