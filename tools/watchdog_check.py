#!/usr/bin/env python3
"""Decide whether a daily Pages delivery is missing (Layer A checker).

The GitHub Actions watchdog downloads the live Pages manifest with curl and
passes the file path via ``--live-manifest``; this script performs no network
I/O itself.  The decision reuses the routine's own gate logic
(:func:`tse_ranking_monitor.gate.select_target_session`), so "missing" means
exactly what the routine would have selected: the oldest unpublished,
already-completed business session.

Output (stdout is exactly one token; diagnostics go to stderr):
  OK                       nothing missing (exit 0)
  MISSING=YYYY-MM-DD       the repository manifest lacks a completed session (exit 1)
  PAGES_STALE=YYYY-MM-DD   repo is current but the live Pages manifest is behind (exit 1)
  (no token)               unreadable/malformed manifest or bad --now (exit 2)

usage:
  python tools/watchdog_check.py [--manifest PATH] [--live-manifest PATH]
     [--now 2026-07-15T19:10:00+09:00]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for _entry in (ROOT / "src", ROOT / "scripts"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from tse_ranking_monitor import gate  # noqa: E402

business_day = gate.business_day
JST = gate.JST


def elog(*args):
    """stdout を1トークンに保つため、診断はすべて stderr へ。"""
    print(*args, file=sys.stderr, flush=True)


def parse_now(value):
    """Parse ``--now`` as JST; naive values are interpreted as JST wall time.

    The default is the current UTC time converted to JST — never a naive
    ``datetime.now()``, whose meaning would depend on the runner's timezone.
    """
    if value is None:
        return datetime.now(timezone.utc).astimezone(JST)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def read_manifest_dates(path, label):
    """Read one manifest fail-closed: missing or malformed raises ValueError.

    ``gate.read_published_dates`` treats a missing file as "no history", which
    is correct for the routine but not for the watchdog: here a missing
    manifest is a configuration error (the workflow checked out the repo, and
    it only passes --live-manifest when curl succeeded), not an empty history.
    """
    manifest = Path(path)
    if not manifest.is_file():
        raise ValueError("%s manifest not found: %s" % (label, manifest))
    try:
        return gate.read_published_dates(manifest)
    except ValueError as exc:
        raise ValueError("%s manifest invalid: %s" % (label, exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="日次配信の欠落判定（watchdog・ネット無し）")
    parser.add_argument("--manifest", default=str(gate.DEFAULT_MANIFEST),
                        help="リポジトリの公開manifest（既定 docs/data/manifest.json）")
    parser.add_argument("--live-manifest", default=None,
                        help="curlで取得済みの本番Pages manifestのローカルパス（任意）")
    parser.add_argument("--now", default=None,
                        help="判定時刻 ISO-8601（テスト用。naiveはJSTと解釈。既定は現在UTC→JST）")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    try:
        now_jst = parse_now(args.now)
    except ValueError as exc:
        elog("[watchdog] ERROR --now が不正: %s" % exc)
        return 2

    try:
        published = read_manifest_dates(args.manifest, "repo")
    except ValueError as exc:
        elog("[watchdog] ERROR %s" % exc)
        return 2

    elog("[watchdog] now=%s repo_latest=%s"
         % (now_jst.isoformat(), max(published, default=None)))
    missing = gate.select_target_session(now_jst, published)
    if missing is not None:
        elog("[watchdog] decision=MISSING（repo manifestに完了済みセッションが無い）")
        print("MISSING=%s" % missing.isoformat())   # ← stdout（1トークン）
        return 1

    if args.live_manifest:
        try:
            live_published = read_manifest_dates(args.live_manifest, "live")
        except ValueError as exc:
            elog("[watchdog] ERROR %s" % exc)
            return 2
        elog("[watchdog] live_latest=%s" % max(live_published, default=None))
        stale = gate.select_target_session(now_jst, live_published)
        if stale is not None:
            elog("[watchdog] decision=PAGES_STALE（repoは最新だがPages側が古い）")
            print("PAGES_STALE=%s" % stale.isoformat())   # ← stdout
            return 1

    elog("[watchdog] decision=OK")
    print("OK")   # ← stdout
    return 0


if __name__ == "__main__":
    sys.exit(main())
