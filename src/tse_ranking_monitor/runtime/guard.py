"""End-of-session reconciliation for unattended routine sessions.

The runtime contract tells the agent to send a failure alert before it stops.
That is prose addressed to a model: when the session ends because the agent gave
up, errored, or simply finished without publishing, nothing runs it.  Three
consecutive silent misses and the 2026-07-27 outage all share that shape.

This module is the code-level enforcement point.  It is driven by session-end
hooks rather than by the agent's own judgement, and it decides from on-disk
evidence alone:

* no session pointer  -> the gate never selected a session (holiday, SKIP, or an
  interactive developer session).  Do nothing.
* ``.delivered``      -> the run completed end to end.  Do nothing.
* ``.notified``       -> an alert already went out.  Do not send a second one.
* otherwise           -> alert, naming the stage the run was inside.

A killed sandbox runs no hooks at all; that case is covered by the durable status
this module also pushes, which an external watchdog reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..publishing import failure_notify
from . import status as run_status
from .telemetry import TelemetryWriter, read_session_pointer, utc_now

DEFAULT_REASON = "session ended without a completed delivery"


def elog(message):
    print("[routine-guard] %s" % message, file=sys.stderr, flush=True)


def _telemetry_dir(root, session):
    return Path(root) / ".work" / str(session) / "telemetry"


def delivered_marker(root, session):
    return _telemetry_dir(root, session) / ".delivered"


def notified_marker(root, session):
    return _telemetry_dir(root, session) / ".notified"


def reconcile(root, *, notify=None, publish=None, reason=DEFAULT_REASON):
    """Alert if the in-flight session ended without delivering.

    Returns one of ``"no-session"``, ``"delivered"``, ``"already-notified"``,
    ``"notified"``, ``"notify-failed"``.  Never raises: a guard that throws would
    reintroduce the silence it exists to remove.
    """
    root = Path(root)
    notify = notify or failure_notify.main
    publish = publish or run_status.publish_status_quietly

    pointer = read_session_pointer(root)
    if not pointer:
        return "no-session"
    session = pointer["session"]

    if delivered_marker(root, session).exists():
        return "delivered"
    if notified_marker(root, session).exists():
        return "already-notified"

    stage = TelemetryWriter(root).last_unfinished_stage(session) or "unknown"

    try:
        status = run_status.collect_status(root, session, died_at=stage, note=reason)
        publish(root, status)
    except Exception as exc:  # noqa: BLE001 — status is observability, never fatal
        elog("WARN ステータスの記録に失敗: %s" % exc)

    try:
        code = notify([
            "--stage", stage,
            "--reason", reason,
            "--session", session,
            "--work-dir", str(root / ".work"),
        ])
    except Exception as exc:  # noqa: BLE001 — see module docstring
        elog("WARN 失敗通知の実行に失敗: %s" % exc)
        return "notify-failed"

    if code != 0:
        elog("WARN 失敗通知が送信できなかった（exit %s）" % code)
        return "notify-failed"

    try:
        marker = notified_marker(root, session)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(utc_now() + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        # Losing the marker only risks a duplicate alert, which is far better
        # than losing the alert itself.
        elog("WARN 通知済みマーカーの書き込みに失敗: %s" % exc)
    elog("session=%s stage=%s の失敗を通知した" % (session, stage))
    return "notified"
