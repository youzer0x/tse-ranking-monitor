#!/usr/bin/env python3
"""Fail-closed verifier for the Claude-branch publication fallback."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tse_ranking_monitor.publishing.promotion import (  # noqa: E402
    PromotionError,
    verify_candidate,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude日次公開commitのmain昇格検証")
    parser.add_argument("--branch", required=True, help="push元branch名（claude/*）")
    parser.add_argument("--head", required=True, help="昇格候補commit/ref")
    parser.add_argument("--base", default="origin/main", help="最新main commit/ref")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        candidate = verify_candidate(args.root, args.branch, args.head, args.base)
    except PromotionError as exc:
        print("NG: %s" % exc, file=sys.stderr)
        return 1
    print(
        "OK: status=%s session=%s head=%s base=%s files=%d"
        % (
            candidate.status,
            candidate.session,
            candidate.head_sha,
            candidate.base_sha,
            len(candidate.changed_paths),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
