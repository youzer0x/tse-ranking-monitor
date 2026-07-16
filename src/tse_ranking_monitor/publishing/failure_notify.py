"""Send a plain-text failure alert from inside the routine (Layer B).

The daily routine used to email only on success, so a failing run produced no
signal at all — three consecutive silent misses went unnoticed.  This module
builds a compact failure report (session, stage, reason, telemetry metrics,
quality repair residuals) and sends it through the same Gmail credentials as
the success notification.

``main`` is deliberately swallow-everything: alerting is best effort and must
never raise, because a crashing alert would mask the original failure and
turn a diagnosable incident back into silence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..runtime.telemetry import summarize_events
from . import gmail

JST = timezone(timedelta(hours=9), name="JST")


def load_telemetry_summary(work_dir, session):
    """Summarize ``.work/<session>/telemetry/events.jsonl``; None if unavailable.

    Individual lines are parsed tolerantly: a torn or partial append (the file
    is written concurrently by hooks) must not cost us the rest of the events.
    """
    if not session:
        return None
    path = Path(work_dir) / str(session) / "telemetry" / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    if not events:
        return None
    return summarize_events(events, session)


def load_repair_residuals(path):
    """Return compact residual lines from a quality_findings.v1 repair payload.

    Each target becomes ``<code|path>: RULE_ID(SEVERITY)`` (e.g.
    ``7013: RANK_UNSOURCED_CAUSAL(WARN)``).  Missing or invalid payloads yield
    None: residuals are context, never a reason to fail the alert itself.
    """
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        return None
    lines = []
    for entry in payload["files"]:
        if not isinstance(entry, dict):
            continue
        for target in entry.get("targets") or []:
            if not isinstance(target, dict):
                continue
            label = target.get("code") or target.get("path") or "$"
            rules = "+".join(str(r) for r in target.get("rule_ids") or [] if r)
            severities = "/".join(str(s) for s in target.get("severities") or [] if s)
            lines.append("%s: %s(%s)" % (label, rules or "UNKNOWN", severities or "?"))
    return lines or None


def build_failure_report(session, stage, reason, telemetry=None, residuals=None,
                         now_jst=None):
    """Build the ``(subject, body)`` pair for one failure alert (pure)."""
    session_label = session or "unknown"
    now_jst = now_jst or datetime.now(JST)
    subject = "[tse-ranking-monitor] 配信失敗 %s｜%s" % (session_label, stage)
    lines = [
        "日次配信が失敗しました。",
        "",
        "session: %s" % session_label,
        "stage: %s" % stage,
        "reason: %s" % reason,
    ]
    if telemetry:
        subagents = telemetry.get("subagents") or {}
        tools = telemetry.get("tools") or {}
        lines.append(
            "telemetry: events=%s subagents=%s/%s tools_ok=%s tools_failed=%s failures=%s"
            % (telemetry.get("events"), subagents.get("completed"),
               subagents.get("started"), tools.get("completed"),
               tools.get("failed"), telemetry.get("failures"))
        )
    if residuals:
        lines.append("quality residuals:")
        lines.extend("  - %s" % residual for residual in residuals)
    lines.append("")
    lines.append("checked at: %s" % now_jst.strftime("%Y-%m-%d %H:%M JST"))
    return subject, "\n".join(lines)


def main(argv=None):
    """Send one failure alert; returns 0 only when the send succeeded.

    Never raises: any failure (missing env, Gmail API error, unreadable
    context files) is reported on stderr and mapped to exit 1 so the caller's
    original failure handling stays in control.
    """
    try:
        parser = argparse.ArgumentParser(description="配信失敗アラートメール（Layer B）")
        parser.add_argument("--stage", required=True,
                            help="失敗したステージ名（gate/stage2/publish 等）")
        parser.add_argument("--reason", required=True, help="失敗理由の一行要約")
        parser.add_argument("--session", default="unknown",
                            help="対象セッション YYYY-MM-DD（不明なら省略）")
        parser.add_argument("--work-dir", default=".work",
                            help="telemetryを探す日次一時ディレクトリ（既定 .work）")
        parser.add_argument("--repair-targets", default=None,
                            help="品質ゲートの repair-targets JSON（残存findingsを添える）")
        args = parser.parse_args(argv)

        telemetry = load_telemetry_summary(args.work_dir, args.session)
        residuals = load_repair_residuals(args.repair_targets)
        subject, body = build_failure_report(
            args.session or "unknown", args.stage, args.reason,
            telemetry=telemetry, residuals=residuals,
        )
        gmail.send_plain_email(subject, body)
        return 0
    except Exception as exc:  # noqa: BLE001 — alerting must never mask the failure
        print("[notify_failure] ERROR %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr, flush=True)
        return 1
