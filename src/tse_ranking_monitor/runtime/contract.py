"""Hash lock for the compact unattended-routine execution contract.

Interactive/manual work still reads the four full source documents.  The
routine may read the compact contract instead, but only after this lock proves
that the compact document and every normative source are unchanged since the
contract was generated.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


CONTRACT_SCHEMA_VERSION = 1
CONTRACT_DOCUMENT = "runbook/RUNTIME_CONTRACT.md"
CONTRACT_LOCK = "runbook/runtime_contract.lock.json"
LOCKED_SOURCES = (
    CONTRACT_DOCUMENT,
    "vendor/tse-ranking-digest/SKILL.md",
    "vendor/tse-ranking-digest/reference/sources.md",
    "runbook/DAILY_ROUTINE.md",
    "specs/MARKET_ANALYSIS.md",
)


def normalized_sha256(path: Path) -> str:
    """Hash file bytes with CRLF normalized so locks are cross-platform."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build_contract_lock(root: Path) -> dict:
    """Build (but do not write) a deterministic contract lock document."""
    root = Path(root)
    missing = [name for name in LOCKED_SOURCES if not (root / name).is_file()]
    if missing:
        raise ValueError("missing contract source(s): %s" % ", ".join(missing))
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "hash_algorithm": "sha256-lf",
        "files": {
            name: normalized_sha256(root / name)
            for name in LOCKED_SOURCES
        },
    }


def write_contract_lock(root: Path, lock_path: str = CONTRACT_LOCK) -> Path:
    """Atomically write a freshly generated lock and return its path."""
    root = Path(root)
    target = root / lock_path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        build_contract_lock(root), ensure_ascii=False, indent=2, sort_keys=False
    ) + "\n"
    temporary = target.with_name(target.name + ".tmp-%s" % os.getpid())
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return target


def verify_contract_lock(root: Path, lock_path: str = CONTRACT_LOCK) -> list[str]:
    """Return all lock failures; an empty list means the contract is usable."""
    root = Path(root)
    path = root / lock_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ["contract lock could not be read: %s" % exc]
    if not isinstance(payload, dict):
        return ["contract lock root must be an object"]

    failures = []
    if payload.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        failures.append("schema_version must be %s" % CONTRACT_SCHEMA_VERSION)
    if payload.get("hash_algorithm") != "sha256-lf":
        failures.append("hash_algorithm must be sha256-lf")

    files = payload.get("files")
    if not isinstance(files, dict):
        failures.append("lock.files must be an object")
        files = {}
    expected_names = set(LOCKED_SOURCES)
    actual_names = set(files)
    for name in sorted(expected_names - actual_names):
        failures.append("unlocked source: %s" % name)
    for name in sorted(actual_names - expected_names):
        failures.append("unexpected locked source: %s" % name)

    for name in LOCKED_SOURCES:
        expected = files.get(name)
        if not isinstance(expected, str) or len(expected) != 64 or any(
                char not in "0123456789abcdef" for char in expected.lower()):
            if name in files:
                failures.append("invalid sha256 for %s" % name)
            continue
        source = root / name
        if not source.is_file():
            failures.append("missing source: %s" % name)
            continue
        actual = normalized_sha256(source)
        if actual != expected:
            failures.append(
                "modified source: %s expected=%s actual=%s"
                % (name, expected, actual)
            )
    return failures
