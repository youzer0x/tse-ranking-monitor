"""Verify the pinned tse-ranking-digest methodology snapshot.

The source of truth lives in news-financial-market.  The snapshot is bundled so
the unattended routine can run without checking out a second repository.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "tse-ranking-digest"
LOCK = VENDOR / "vendor.lock.json"


def normalized_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"NG: methodology lock could not be read: {exc}")
        return 1

    locked_files = lock.get("files") or {}
    failures = []
    if not isinstance(locked_files, dict) or not locked_files:
        failures.append("lock.files must be a non-empty object")
        locked_files = {}
    commit = lock.get("commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit.lower()
    ):
        failures.append("lock.commit must be a full 40-character Git SHA")

    for name, expected in locked_files.items():
        path = VENDOR / name
        if not path.is_file():
            failures.append(f"missing: {name}")
            continue
        actual = normalized_sha256(path)
        if actual != expected:
            failures.append(f"modified: {name} expected={expected} actual={actual}")

    actual_files = {
        path.relative_to(VENDOR).as_posix()
        for path in VENDOR.rglob("*")
        if path.is_file() and path != LOCK and "__pycache__" not in path.parts
    }
    unexpected = sorted(actual_files - set(locked_files))
    for name in unexpected:
        failures.append(f"unlocked file: {name}")

    if failures:
        for failure in failures:
            print(f"NG: {failure}")
        return 1
    print(
        "OK: methodology snapshot matches "
        f"{lock.get('source')}@{str(lock.get('commit', 'unknown'))[:7]} "
        f"({len(locked_files)} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
