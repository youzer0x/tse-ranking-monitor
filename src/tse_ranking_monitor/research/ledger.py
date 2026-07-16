"""Reserve Stage2 dispatch attempts against the manifest budget.

The research manifest is the single source of truth for how many subagent
dispatches a session may spend.  ``reserve`` increments the persistent
reservation ledger under an inter-process mutex so concurrent dispatchers
cannot overshoot the per-batch or total limits.

Exit codes: 0 reserved, 1 IO/parse error, 3 budget exhausted (no write),
4 misuse — unknown batch id or batch not pending (no write).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .plan import PER_BATCH_DISPATCH_LIMIT, TOTAL_DISPATCH_LIMIT, _atomic_write_json


LOCK_TIMEOUT_S = 5.0
LOCK_STALE_S = 30.0


def _acquire_lock(lock: Path) -> None:
    """Take a mkdir-based inter-process mutex (see runtime/telemetry.py)."""
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            lock.mkdir()
            return
        except (FileExistsError, PermissionError):
            # Windows may surface a concurrent create/remove race as
            # ERROR_ACCESS_DENIED instead of ERROR_ALREADY_EXISTS.
            try:
                if time.time() - lock.stat().st_mtime >= LOCK_STALE_S:
                    # Break a lock orphaned by a crashed holder.
                    lock.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"reserve lock timed out: {lock}")
            time.sleep(0.005)


def _release_lock(lock: Path) -> None:
    try:
        lock.rmdir()
    except FileNotFoundError:
        pass


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def reserve(research_dir: str | os.PathLike[str], batch_id: str) -> int:
    """Atomically reserve one dispatch attempt for ``batch_id``; return exit code."""
    manifest_path = Path(research_dir) / "manifest.json"
    lock = manifest_path.with_name(manifest_path.name + ".reserve-lock")
    _acquire_lock(lock)
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if not isinstance(manifest, dict):
            raise ValueError("manifest root must be an object")
        budget = manifest.get("dispatch_budget")
        if not isinstance(budget, dict):
            budget = {}
        per_batch_limit = _count(
            budget.get("per_batch_limit", PER_BATCH_DISPATCH_LIMIT),
            "dispatch_budget.per_batch_limit",
        )
        total_limit = _count(
            budget.get("total_limit", TOTAL_DISPATCH_LIMIT),
            "dispatch_budget.total_limit",
        )
        entry = next(
            (
                item
                for item in manifest.get("batches") or []
                if isinstance(item, dict) and item.get("batch_id") == batch_id
            ),
            None,
        )
        if entry is None or entry.get("status") != "pending":
            reason = "unknown batch" if entry is None else f"status={entry.get('status')}"
            print(f"[reserve_dispatch] REFUSED {batch_id}: {reason}", file=sys.stderr)
            return 4
        ledger = manifest.get("ledger")
        if not isinstance(ledger, dict):
            ledger = {}
        reservations = ledger.get("reservations")
        if not isinstance(reservations, dict):
            reservations = {}
        attempts = _count(reservations.get(batch_id, 0), f"ledger.reservations.{batch_id}")
        total = _count(ledger.get("total_reserved", 0), "ledger.total_reserved")
        if attempts + 1 > per_batch_limit or total + 1 > total_limit:
            print(
                f"[reserve_dispatch] EXHAUSTED {batch_id}: "
                f"attempts={attempts}/{per_batch_limit} total={total}/{total_limit}",
                file=sys.stderr,
            )
            return 3
        reservations[batch_id] = attempts + 1
        ledger["reservations"] = reservations
        ledger["total_reserved"] = total + 1
        manifest["ledger"] = ledger
        _atomic_write_json(manifest_path, manifest)
        print(
            f"[reserve_dispatch] OK {batch_id} "
            f"attempt={attempts + 1} total={total + 1}/{total_limit}"
        )
        return 0
    finally:
        _release_lock(lock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reserve one Stage2 dispatch attempt against the manifest budget"
    )
    parser.add_argument(
        "--research-dir", required=True, help="Directory containing manifest.json"
    )
    parser.add_argument("--batch", required=True, help="Batch id, e.g. batch-003")
    args = parser.parse_args(argv)
    try:
        return reserve(args.research_dir, args.batch)
    except (OSError, ValueError, TimeoutError) as exc:
        print(f"[reserve_dispatch] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
