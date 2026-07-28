"""Claude hook: alert when a routine session ends without delivering.

Wired to session-end events so the runtime contract's failure notification stops
depending on the agent being alive and willing to run it.  Thin wrapper: the
decision logic lives in ``tse_ranking_monitor.runtime.guard`` so it is testable.

Contract, matching the other observability hooks:
  * stdout stays empty so this never injects context into the model
  * diagnostics go to stderr
  * always exit 0 -- the guard must never block or fail a session
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main():
    try:
        # The payload is not needed to decide anything -- on-disk evidence is the
        # authority -- but draining stdin keeps the hook protocol well behaved.
        try:
            json.load(sys.stdin)
        except (ValueError, OSError):
            pass

        from tse_ranking_monitor.runtime import guard

        outcome = guard.reconcile(ROOT)
        print("[routine-guard] outcome=%s" % outcome, file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 — a guard that raises is a guard that blocks
        print("[routine-guard] error=%s: %s" % (type(exc).__name__, exc),
              file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
