#!/usr/bin/env python3
"""Claude hook + stage CLI for unattended-routine telemetry.

With no arguments, reads a Claude hook payload from stdin.  Routine scripts may
also mark deterministic stage spans:

  python .claude/hooks/runtime_telemetry.py stage start gate --session 2026-07-15
  python .claude/hooks/runtime_telemetry.py stage end gate --session 2026-07-15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tse_ranking_monitor.runtime.telemetry import (  # noqa: E402
    TelemetryWriter,
    compact_line,
    event_from_hook,
)


def _hook_main():
    try:
        payload = json.load(sys.stdin)
        event = event_from_hook(payload, os.environ)
        TelemetryWriter(ROOT).append(event)
        # stderr is diagnostic output for the configured non-blocking events;
        # stdout remains empty so this hook never injects model context.
        print(compact_line(event), file=sys.stderr)
    except Exception as exc:
        # Observability must never block the ranking pipeline.
        print("[routine-metrics] logger_error=%s" % str(exc)[:200], file=sys.stderr)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _hook_main()
    parser = argparse.ArgumentParser(description="Routine telemetry stage marker")
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("phase", choices=("start", "end"))
    stage.add_argument("name")
    stage.add_argument("--session", required=True)
    stage.add_argument("--status")
    args = parser.parse_args(argv)
    writer = TelemetryWriter(ROOT)
    path = writer.record_stage(args.session, args.name, args.phase, args.status)
    print("[routine-metrics] stage_%s session=%s stage=%s path=%s"
          % (args.phase, args.session, args.name, path.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
