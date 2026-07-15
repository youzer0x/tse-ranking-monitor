#!/usr/bin/env python3
"""Generate or verify the compact routine contract hash lock.

Usage:
  python tools/runtime_contract.py generate
  python tools/runtime_contract.py check

Defaults are ``runbook/RUNTIME_CONTRACT.md`` and
``runbook/runtime_contract.lock.json``.  ``check`` is fail-closed and must run
before an unattended routine starts Stage1 or changes published artifacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tse_ranking_monitor.runtime.contract import (  # noqa: E402
    CONTRACT_LOCK,
    LOCKED_SOURCES,
    verify_contract_lock,
    write_contract_lock,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="無人Routine実行契約のhash lock")
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--contract", "--lock", dest="lock", default=CONTRACT_LOCK,
                        help="リポジトリroot相対のcontract lockパス")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.action == "generate":
        try:
            path = write_contract_lock(root, args.lock)
        except (OSError, ValueError) as exc:
            print("NG: runtime contract lock generation failed: %s" % exc)
            return 1
        print("OK: wrote %s (%d sources)" % (path.relative_to(root), len(LOCKED_SOURCES)))
        return 0

    failures = verify_contract_lock(root, args.lock)
    if failures:
        for failure in failures:
            print("NG: %s" % failure)
        return 1
    print("OK: runtime contract and %d normative sources match lock" % len(LOCKED_SOURCES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
