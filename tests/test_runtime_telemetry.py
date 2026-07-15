"""Secret-free Claude hook telemetry and stage timing tests."""

import json
from concurrent.futures import ThreadPoolExecutor

from tse_ranking_monitor.runtime import telemetry


def test_hook_event_records_metadata_and_sizes_without_payload_content():
    secret = "api-key-must-not-be-logged"
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "run-1",
        "agent_id": "agent-2",
        "agent_type": "stock-factor-researcher",
        "tool_name": "WebFetch",
        "tool_use_id": "tool-3",
        "tool_input": {"url": "https://example.test", "token": secret},
        "error": "request failed: %s" % secret,
        "effort": {"level": "max"},
    }

    event = telemetry.event_from_hook(payload, {"TSE_SESSION": "2026-07-15"})
    serialized = json.dumps(event, ensure_ascii=False)

    assert event["event"] == "tool_end"
    assert event["status"] == "failed"
    assert event["error_type"] == "tool_failure"
    assert event["agent_type"] == "stock-factor-researcher"
    assert event["effort"] == "max"
    assert event["tokens"]["source"] == "unavailable"
    assert event["input_chars"] > 0
    assert secret not in serialized


def test_hook_event_uses_actual_tokens_only_when_hook_supplies_usage():
    event = telemetry.event_from_hook({
        "hook_event_name": "PostToolUse",
        "tool_name": "WebSearch",
        "usage": {"input_tokens": 12, "output_tokens": 5},
    }, {})

    assert event["tokens"] == {
        "source": "hook_payload", "input_tokens": 12, "output_tokens": 5
    }
    assert "tokens=actual" in telemetry.compact_line(event)


def test_writer_concurrently_appends_parseable_json_lines(tmp_path):
    writer = telemetry.TelemetryWriter(tmp_path)

    def append(index):
        writer.append({"event": "tool_end", "index": index}, "2026-07-15")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))

    lines = writer.event_path("2026-07-15").read_text(encoding="utf-8").splitlines()
    decoded = [json.loads(line) for line in lines]
    assert len(decoded) == 40
    assert {event["index"] for event in decoded} == set(range(40))


def test_stage_end_records_duration_and_summary(tmp_path, monkeypatch):
    writer = telemetry.TelemetryWriter(tmp_path)
    clock = iter((1_000_000_000, 1_345_000_000))
    monkeypatch.setattr(telemetry.time, "time_ns", lambda: next(clock))

    writer.record_stage("2026-07-15", "stage1", "start")
    writer.record_stage("2026-07-15", "stage1", "end")

    events = [
        json.loads(line)
        for line in writer.event_path("2026-07-15").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["duration_ms"] == 345
    summary = telemetry.summarize_events(events, "2026-07-15")
    assert summary["stages_ms"] == {"stage1": 345}
    assert summary["tokens"]["source"] == "unavailable"
