"""Deterministically re-plan Stage2 batches flagged by quality validators.

2026-07-15 incident: re-dispatched batches carried no information about what
the validator flagged nor the previous research output, so agents repeated
the same mistakes.  This module injects a ``repair_context`` block into each
affected batch file so the re-research agent sees (1) the exact findings per
flagged code, (2) its own previous conclusion as the base to fix, and (3) the
untouched conclusions it must carry forward verbatim.

Digest discipline mirrors :mod:`.plan` exactly: the batch ``input_digest`` is
popped, ``repair_context`` is set, the digest is recomputed over the payload
without the digest key, and the manifest entry gets the new digest/size and is
re-queued as ``pending``; the manifest top-level ``input_digest`` is then
recomputed with the plan recipe.  The previous result file is intentionally
left in place: the digest change alone makes it stale, and both
``write_research_plan`` and ``compile_research_results`` already treat stale
checkpoints as invalid.

State machine per affected batch (``repair_attempts`` on the manifest entry
counts how many times a valid completed result was invalidated by repair):

* completed checkpoint  -> inject a fresh ``repair_context`` and increment
  ``repair_attempts``; refuse before any write when the budget is spent
  (all-or-nothing across every affected batch).
* pending, identical context -> no-op (no write, no increment).
* pending, different/absent context -> merge targets (union by code, dedupe
  rule_ids/severities/messages), no increment.  A batch that was never
  researched gets ``previous: null`` / ``carry_forward: []``.

``repair_context.attempt`` always mirrors the manifest ``repair_attempts``
value at write time (0 for a never-completed batch).

This module never touches the dispatch ledger or budget — reservation
accounting belongs to :mod:`.ledger` at dispatch time.

Exit codes: 0 success (injected/merged/no-op), 1 IO/validation error,
3 attempt budget exceeded (no write), 4 payload has no code-bearing targets.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .plan import (
    _atomic_write_json,
    _canonical_digest,
    _checkpoint_status,
    _compact_json_size,
)


# Kept in sync with quality/market.py (the payload producer); duplicated as a
# literal so the research package does not import the market-assembly stack.
FINDINGS_SCHEMA_VERSION = "quality_findings.v1"
SUPPORTED_VALIDATOR = "ranking"
MESSAGE_MAX_CHARS = 500

# Trimmed subset of a result item carried into repair_context.  Enough for the
# agent to reuse a conclusion verbatim (factor text, tag, confidence, sources,
# market note) while bounding injected batch size; claims/checks/status are
# re-derived by the agent on re-research.
REPAIR_CARRY_FIELDS = ("code", "factor", "factor_kind", "confidence", "sources", "market_note")


class RepairBudgetError(RuntimeError):
    """Raised before any write when a completed batch has no repair attempts left."""


class NoCodeTargetsError(ValueError):
    """Raised when the payload contains no code-bearing repair targets."""


def trim_carry_item(item: Any) -> dict[str, Any] | None:
    """Project a result item onto the fixed carry-forward field subset."""
    if not isinstance(item, dict):
        return None
    return {field: item.get(field) for field in REPAIR_CARRY_FIELDS}


def _read_json(path: Path, label: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {path}: {exc}") from exc


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _targets_by_code(payload: Any) -> dict[str, dict[str, list[str]]]:
    """Validate the findings payload and merge its targets per ranking code."""
    if not isinstance(payload, dict):
        raise ValueError("repair payload root must be an object")
    if payload.get("schema_version") != FINDINGS_SCHEMA_VERSION:
        raise ValueError(f"repair payload schema_version must be {FINDINGS_SCHEMA_VERSION}")
    if payload.get("validator") != SUPPORTED_VALIDATOR:
        raise ValueError(
            "repair payload validator must be 'ranking' "
            "(market findings point at narrative paths, not research batches)"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("repair payload.files must be an array")
    merged: dict[str, dict[str, list[str]]] = {}
    for file_index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ValueError(f"repair payload.files[{file_index}] must be an object")
        targets = entry.get("targets")
        if targets is None:
            targets = []
        if not isinstance(targets, list):
            raise ValueError(f"repair payload.files[{file_index}].targets must be an array")
        for target_index, target in enumerate(targets):
            if not isinstance(target, dict):
                raise ValueError(
                    f"repair payload.files[{file_index}].targets[{target_index}] "
                    "must be an object"
                )
            code = str(target.get("code") or "").strip()
            if not code:
                continue  # path-only findings cannot be mapped to a batch
            accumulated = merged.setdefault(
                code, {"rule_ids": [], "severities": [], "messages": []}
            )
            for field in ("rule_ids", "severities"):
                for value in _string_list(target.get(field)):
                    if value not in accumulated[field]:
                        accumulated[field].append(value)
            for message in _string_list(target.get("messages")):
                trimmed = message[:MESSAGE_MAX_CHARS]
                if trimmed not in accumulated["messages"]:
                    accumulated["messages"].append(trimmed)
    if not merged:
        raise NoCodeTargetsError("repair payload carries no code-bearing targets")
    return merged


def _load_result_items(path: Path) -> dict[str, dict[str, Any]]:
    """Best-effort read of the existing result file (a stale one still informs)."""
    try:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return {}
    items: dict[str, dict[str, Any]] = {}
    for item in result["items"]:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            if code:
                items.setdefault(code, item)
    return items


def _fresh_context(
    attempt: int,
    flagged: list[str],
    incoming: dict[str, dict[str, list[str]]],
    batch_codes: list[str],
    result_items: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "targets": [
            {
                "code": code,
                "rule_ids": list(incoming[code]["rule_ids"]),
                "severities": list(incoming[code]["severities"]),
                "messages": list(incoming[code]["messages"]),
                "previous": trim_carry_item(result_items.get(code)),
            }
            for code in sorted(flagged)
        ],
        "carry_forward": [
            trim_carry_item(result_items[code])
            for code in sorted(batch_codes)
            if code not in flagged and code in result_items
        ],
    }


def _merge_context(
    existing: dict[str, Any],
    flagged: list[str],
    incoming: dict[str, dict[str, list[str]]],
    result_items: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Union new findings into an existing context; a flagged code leaves carry_forward."""
    merged = json.loads(json.dumps(existing, ensure_ascii=False))
    targets = [item for item in merged.get("targets") or [] if isinstance(item, dict)]
    by_code = {str(item.get("code") or "").strip(): item for item in targets}
    carry_forward = [
        item for item in merged.get("carry_forward") or [] if isinstance(item, dict)
    ]
    carry_by_code = {str(item.get("code") or "").strip(): item for item in carry_forward}
    for code in flagged:
        finding = incoming[code]
        target = by_code.get(code)
        if target is None:
            previous = trim_carry_item(carry_by_code.get(code))
            if previous is None:
                previous = trim_carry_item(result_items.get(code))
            by_code[code] = {
                "code": code,
                "rule_ids": list(finding["rule_ids"]),
                "severities": list(finding["severities"]),
                "messages": list(finding["messages"]),
                "previous": previous,
            }
            continue
        for field in ("rule_ids", "severities", "messages"):
            values = target.get(field)
            if not isinstance(values, list):
                values = []
                target[field] = values
            for value in finding[field]:
                if value not in values:
                    values.append(value)
    merged["targets"] = [by_code[code] for code in sorted(by_code)]
    merged["carry_forward"] = [
        item for item in carry_forward
        if str(item.get("code") or "").strip() not in by_code
    ]
    return merged


def _attempts(entry: dict[str, Any], batch_id: str) -> int:
    value = entry.get("repair_attempts", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"manifest entry {batch_id}.repair_attempts must be a non-negative integer"
        )
    return value


def apply_repair_targets(
    research_dir: str | os.PathLike[str],
    repair_payload: Any,
    *,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Inject or merge repair_context into flagged batches; return an op summary.

    Returns ``{"injected": [...], "merged": [...], "noop": [...],
    "attempts": {batch_id: n}}`` where ``attempts`` reports the post-operation
    ``repair_attempts`` of every affected batch.  Raises ``ValueError`` on
    invalid payload/plan, :class:`NoCodeTargetsError` when nothing is
    addressable, and :class:`RepairBudgetError` before any write when a
    completed batch is out of attempts (all-or-nothing).
    """
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    root = Path(research_dir)
    incoming = _targets_by_code(repair_payload)

    manifest = _read_json(root / "manifest.json", "manifest")
    if not isinstance(manifest, dict):
        raise ValueError("manifest root must be an object")
    for key in ("session_date", "ranking_codes"):
        if key not in manifest:
            raise ValueError(f"manifest.{key} is required")
    entries = manifest.get("batches")
    if not isinstance(entries, list):
        raise ValueError("manifest.batches must be an array")
    code_to_entry: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest.batches[{index}] must be an object")
        for raw_code in entry.get("codes") or []:
            code = str(raw_code or "").strip()
            if code in code_to_entry:
                raise ValueError(f"code assigned to multiple batches: {code}")
            code_to_entry[code] = entry
    unknown = sorted(set(incoming) - set(code_to_entry))
    if unknown:
        raise ValueError(f"repair targets reference unknown ranking codes: {unknown}")

    flagged_by_batch: dict[str, list[str]] = {}
    entry_by_batch: dict[str, dict[str, Any]] = {}
    for code in incoming:
        entry = code_to_entry[code]
        batch_id = str(entry.get("batch_id") or "").strip()
        if not batch_id:
            raise ValueError(f"manifest entry for code {code} lacks batch_id")
        entry_by_batch[batch_id] = entry
        flagged_by_batch.setdefault(batch_id, []).append(code)

    # Evaluate every affected batch before writing anything: a spent attempt
    # budget anywhere aborts the whole run (exit 3, no partial repair).
    operations: list[dict[str, Any]] = []
    exhausted: list[str] = []
    for batch_id in sorted(flagged_by_batch):
        entry = entry_by_batch[batch_id]
        flagged = sorted(flagged_by_batch[batch_id])
        batch_path = root / str(entry.get("path") or "")
        result_path = root / str(entry.get("result_path") or "")
        batch = _read_json(batch_path, f"input batch {batch_id}")
        if not isinstance(batch, dict):
            raise ValueError(f"input batch {batch_id} root must be an object")
        if batch.get("batch_id") != batch_id:
            raise ValueError(f"input batch {batch_id} has mismatched batch_id")
        if batch.get("input_digest") != entry.get("input_digest"):
            raise ValueError(f"input batch {batch_id} digest differs from manifest")
        attempts = _attempts(entry, batch_id)
        batch_codes = [str(code or "").strip() for code in entry.get("codes") or []]
        result_items = _load_result_items(result_path)
        operation = {
            "batch_id": batch_id,
            "entry": entry,
            "batch": batch,
            "path": batch_path,
            "attempts": attempts,
        }
        if _checkpoint_status(result_path, batch) == "complete":
            # (a) A valid completed result is about to be invalidated.
            if attempts >= max_attempts:
                exhausted.append(batch_id)
                continue
            operation.update(
                kind="inject",
                attempts=attempts + 1,
                context=_fresh_context(
                    attempts + 1, flagged, incoming, batch_codes, result_items
                ),
            )
        else:
            existing = batch.get("repair_context")
            if isinstance(existing, dict):
                context = _merge_context(existing, flagged, incoming, result_items)
                if context == existing:
                    operation.update(kind="noop", context=None)  # (b)
                else:
                    operation.update(kind="merge", context=context)  # (c)
            else:
                # (c) pending without context — including a never-researched
                # batch, whose previous/carry_forward stay null/empty.
                operation.update(
                    kind="merge",
                    context=_fresh_context(
                        attempts, flagged, incoming, batch_codes, result_items
                    ),
                )
        operations.append(operation)
    if exhausted:
        raise RepairBudgetError(
            f"repair attempt budget exhausted (max_attempts={max_attempts}) "
            f"for: {exhausted}"
        )

    summary: dict[str, Any] = {"injected": [], "merged": [], "noop": [], "attempts": {}}
    wrote = False
    for operation in operations:
        summary["attempts"][operation["batch_id"]] = operation["attempts"]
        if operation["kind"] == "noop":
            summary["noop"].append(operation["batch_id"])
            continue
        payload = operation["batch"]
        # Digest handling mirrors plan.append_batch: digest is computed over
        # the payload without its input_digest key, then reattached.
        payload.pop("input_digest")
        payload["repair_context"] = operation["context"]
        payload["input_digest"] = _canonical_digest(payload)
        _atomic_write_json(operation["path"], payload, compact=True)
        entry = operation["entry"]
        entry["input_digest"] = payload["input_digest"]
        entry["input_bytes"] = _compact_json_size(payload)
        entry["status"] = "pending"
        if operation["kind"] == "inject":
            entry["repair_attempts"] = operation["attempts"]
            summary["injected"].append(operation["batch_id"])
        else:
            summary["merged"].append(operation["batch_id"])
        wrote = True
    if wrote:
        manifest["input_digest"] = _canonical_digest(
            {
                "session_date": manifest["session_date"],
                "codes": manifest["ranking_codes"],
                "batches": [item.get("input_digest") for item in entries],
            }
        )
        _atomic_write_json(root / "manifest.json", manifest)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inject quality repair context into Stage2 research batches"
    )
    parser.add_argument(
        "--research-dir", required=True, help="Directory containing manifest/batches/results"
    )
    parser.add_argument(
        "--repair-targets",
        required=True,
        help="quality_findings.v1 JSON from validate_ranking_quality --repair-targets",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Max completed-result invalidations per batch (default 2)",
    )
    args = parser.parse_args(argv)
    try:
        with open(args.repair_targets, encoding="utf-8") as handle:
            payload = json.load(handle)
        summary = apply_repair_targets(
            args.research_dir, payload, max_attempts=args.max_attempts
        )
    except NoCodeTargetsError as exc:
        print(f"[repair_research_plan] NO-TARGETS: {exc}", file=os.sys.stderr)
        return 4
    except RepairBudgetError as exc:
        print(f"[repair_research_plan] EXHAUSTED: {exc}", file=os.sys.stderr)
        return 3
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[repair_research_plan] ERROR: {exc}", file=os.sys.stderr)
        return 1
    for kind in ("injected", "merged", "noop"):
        for batch_id in summary[kind]:
            print(
                f"[repair_research_plan] {batch_id}: {kind} "
                f"(repair_attempts={summary['attempts'][batch_id]})",
                file=os.sys.stderr,
            )
    attempts = ",".join(
        f"{batch_id}:{summary['attempts'][batch_id]}"
        for batch_id in sorted(summary["attempts"])
    )
    print(
        "[repair_research_plan] OK: "
        f"injected={len(summary['injected'])} merged={len(summary['merged'])} "
        f"noop={len(summary['noop'])} attempts=[{attempts}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
