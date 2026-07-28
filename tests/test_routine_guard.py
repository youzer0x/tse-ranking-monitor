"""End-of-session guard: the code-level enforcement of the failure alert.

Gmail and status publication are faked; nothing here touches the network.
"""
import json
from pathlib import Path

from tse_ranking_monitor.runtime import guard
from tse_ranking_monitor.runtime import telemetry

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / ".claude" / "settings.json"


class _Recorder:
    """Captures notify/publish calls in place of Gmail and git."""

    def __init__(self, code=0):
        self.calls = []
        self.statuses = []
        self.code = code

    def notify(self, argv):
        self.calls.append(argv)
        return self.code

    def publish(self, _root, status):
        self.statuses.append(status)
        return "0" * 40


def _start_session(tmp_path, session="2026-07-27", stage="research"):
    telemetry.write_session_pointer(tmp_path, session)
    if stage:
        telemetry.TelemetryWriter(tmp_path).record_stage(session, stage, "start")
    return session


def _argv_value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_no_pointer_means_no_session_was_ever_selected(tmp_path):
    """A holiday, a SKIP, or an interactive session must not raise an alert."""
    recorder = _Recorder()

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=recorder.publish)

    assert outcome == "no-session"
    assert recorder.calls == []


def test_delivered_session_is_silent(tmp_path):
    session = _start_session(tmp_path)
    guard.delivered_marker(tmp_path, session).write_text("ok\n", encoding="utf-8")
    recorder = _Recorder()

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=recorder.publish)

    assert outcome == "delivered"
    assert recorder.calls == []


def test_undelivered_session_alerts_and_names_the_stage(tmp_path):
    session = _start_session(tmp_path, stage="stage2")
    recorder = _Recorder()

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=recorder.publish)

    assert outcome == "notified"
    assert len(recorder.calls) == 1
    argv = recorder.calls[0]
    assert _argv_value(argv, "--session") == session
    assert _argv_value(argv, "--stage") == "stage2"
    assert _argv_value(argv, "--reason") == guard.DEFAULT_REASON
    assert guard.notified_marker(tmp_path, session).exists()

    assert recorder.statuses[-1]["died_at"] == "stage2"
    assert recorder.statuses[-1]["delivered"] is False


def test_session_with_no_stage_markers_still_alerts(tmp_path):
    """Dying before the first stage marker is exactly the 2026-07-27 shape."""
    _start_session(tmp_path, stage=None)
    recorder = _Recorder()

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=recorder.publish)

    assert outcome == "notified"
    assert _argv_value(recorder.calls[0], "--stage") == "unknown"


def test_second_run_does_not_send_a_duplicate_alert(tmp_path):
    _start_session(tmp_path)
    recorder = _Recorder()

    assert guard.reconcile(
        tmp_path, notify=recorder.notify, publish=recorder.publish) == "notified"
    assert guard.reconcile(
        tmp_path, notify=recorder.notify, publish=recorder.publish) == "already-notified"

    assert len(recorder.calls) == 1


def test_failed_send_is_not_marked_notified_so_a_retry_can_happen(tmp_path):
    session = _start_session(tmp_path)
    recorder = _Recorder(code=1)

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=recorder.publish)

    assert outcome == "notify-failed"
    assert not guard.notified_marker(tmp_path, session).exists()


def test_a_raising_notifier_never_propagates(tmp_path):
    _start_session(tmp_path)

    def _boom(_argv):
        raise RuntimeError("gmail exploded")

    outcome = guard.reconcile(tmp_path, notify=_boom, publish=lambda *_a: None)

    assert outcome == "notify-failed"


def test_a_raising_status_publisher_does_not_stop_the_alert(tmp_path):
    """Status is observability; the alert is the point."""
    _start_session(tmp_path)
    recorder = _Recorder()

    def _boom(*_args):
        raise RuntimeError("push rejected")

    outcome = guard.reconcile(tmp_path, notify=recorder.notify, publish=_boom)

    assert outcome == "notified"
    assert len(recorder.calls) == 1


# --- the wiring itself must not be silently removable ------------------------

def _hook_commands(settings, event):
    commands = []
    for entry in settings["hooks"].get(event, []):
        for hook in entry.get("hooks", []):
            commands.append((hook.get("command", ""), hook.get("timeout")))
    return commands


def test_guard_is_wired_to_the_session_end_events():
    """Without this test the enforcement point can be deleted unnoticed.

    StopFailure alone is not enough: it has never been observed to fire, while
    SessionEnd fires on both normal and abnormal termination.
    """
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))

    for event in ("SessionEnd", "StopFailure"):
        commands = _hook_commands(settings, event)
        guard_hooks = [
            (command, timeout) for command, timeout in commands
            if "routine_guard.py" in command
        ]
        assert guard_hooks, "%s must run routine_guard.py" % event
        for command, timeout in guard_hooks:
            assert "${CLAUDE_PROJECT_DIR}" in command, "hook path must not be relative"
            # Sending mail over the network does not fit in the 5s used by the
            # passive telemetry hooks.
            assert timeout is not None and timeout >= 30, (
                "%s guard timeout %r is too short to send an alert" % (event, timeout)
            )


def test_telemetry_hook_stays_wired_alongside_the_guard():
    """Observation and enforcement are separate; keep both."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))

    for event in ("SessionEnd", "StopFailure"):
        commands = [command for command, _timeout in _hook_commands(settings, event)]
        assert any("runtime_telemetry.py" in command for command in commands)


def test_hook_scripts_referenced_by_settings_exist():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))

    for entries in settings["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if ".claude/hooks/" not in command:
                    continue
                name = command.split(".claude/hooks/")[1].split('"')[0]
                assert (ROOT / ".claude" / "hooks" / name).exists(), name
