"""Validate agent batch results and compile the Stage2 evidence ledger."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .plan import (
    BATCH_SCHEMA_VERSION,
    CHECK_NAMES,
    PLAN_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    _atomic_write_json,
)
from .repair import trim_carry_item


EVIDENCE_SCHEMA_VERSION = "evidence.v1"
VALID_KINDS = {"開示", "報道", "テーマ"}
VALID_STATUSES = {"complete", "unresolved"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_CHECK_STATES = {"done", "na", "unavailable"}
VALID_SOURCE_TYPES = {"tdnet", "company_ir", "edinet", "article"}
VALID_SOURCE_WINDOWS = {"material", "prior"}


class ResearchValidationError(ValueError):
    """Raised when strict compilation finds missing or invalid evidence."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate_published_at(value: Any, label: str) -> str:
    raw = _required_text(value, label)
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601") from exc
    return raw


def _safe_child(root: Path, relative: Any, label: str) -> Path:
    rel = _required_text(relative, label)
    candidate = (root / rel).resolve()
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes research directory") from exc
    return candidate


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


def _validate_source(source: Any, label: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError(f"{label} must be an object")
    source_id = _required_text(source.get("id"), f"{label}.id")
    source_type = source.get("source_type")
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"{label}.source_type must be one of {sorted(VALID_SOURCE_TYPES)}"
        )
    window = source.get("window")
    if window not in VALID_SOURCE_WINDOWS:
        raise ValueError(
            f"{label}.window must be one of {sorted(VALID_SOURCE_WINDOWS)}"
        )
    url = _required_text(source.get("url"), f"{label}.url")
    if not _valid_http_url(url):
        raise ValueError(f"{label}.url must be http(s)")
    return {
        "id": source_id,
        "label": _required_text(source.get("label"), f"{label}.label"),
        "url": url,
        "source_type": source_type,
        "published_at": _validate_published_at(
            source.get("published_at"), f"{label}.published_at"
        ),
        "window": window,
    }


def _validate_claim(claim: Any, label: str, source_ids: set[str]) -> dict[str, Any]:
    if not isinstance(claim, dict):
        raise ValueError(f"{label} must be an object")
    ids = claim.get("source_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ValueError(f"{label}.source_ids must be a string array")
    normalized_ids = [item.strip() for item in ids]
    if any(not item for item in normalized_ids):
        raise ValueError(f"{label}.source_ids cannot contain empty values")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError(f"{label}.source_ids contains duplicates")
    unknown = sorted(set(normalized_ids) - source_ids)
    if unknown:
        raise ValueError(f"{label}.source_ids contains unknown ids: {unknown}")
    return {
        "text": _required_text(claim.get("text"), f"{label}.text"),
        "source_ids": normalized_ids,
    }


def _validate_result_item(
    raw: Any, input_item: dict[str, Any], label: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    code = _required_text(raw.get("code"), f"{label}.code")
    if code != input_item["code"]:
        raise ValueError(f"{label}.code does not match batch input")
    status = raw.get("status")
    if status not in VALID_STATUSES:
        raise ValueError(f"{label}.status must be one of {sorted(VALID_STATUSES)}")
    confidence = raw.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(
            f"{label}.confidence must be one of {sorted(VALID_CONFIDENCE)}"
        )
    factor = _required_text(raw.get("factor"), f"{label}.factor")
    factor_kind = raw.get("factor_kind")
    if factor_kind not in VALID_KINDS:
        raise ValueError(f"{label}.factor_kind must be one of {sorted(VALID_KINDS)}")

    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError(f"{label}.sources must be an array")
    sources = [
        _validate_source(source, f"{label}.sources[{index}]")
        for index, source in enumerate(raw_sources)
    ]
    source_ids = [source["id"] for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError(f"{label}.sources contains duplicate ids")
    if factor_kind in {"開示", "報道"} and not sources:
        raise ValueError(f"{label}.sources is required for {factor_kind}")
    if factor_kind == "開示" and not any(
        source["source_type"] in {"tdnet", "company_ir"}
        and source["window"] == "material"
        for source in sources
    ):
        raise ValueError(f"{label}: 開示 requires a material TDnet/company IR source")
    if factor_kind == "報道" and not any(
        source["window"] == "material" for source in sources
    ):
        raise ValueError(f"{label}: 報道 requires a material-window source")

    raw_claims = raw.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ValueError(f"{label}.claims must be a non-empty array")
    claims = [
        _validate_claim(claim, f"{label}.claims[{index}]", set(source_ids))
        for index, claim in enumerate(raw_claims)
    ]

    checks = raw.get("checks")
    if not isinstance(checks, dict):
        raise ValueError(f"{label}.checks must be an object")
    if set(checks) != set(CHECK_NAMES):
        raise ValueError(f"{label}.checks must contain exactly {list(CHECK_NAMES)}")
    normalized_checks = {}
    for name in CHECK_NAMES:
        value = checks[name]
        if value not in VALID_CHECK_STATES:
            raise ValueError(
                f"{label}.checks.{name} must be one of {sorted(VALID_CHECK_STATES)}"
            )
        normalized_checks[name] = value
    # requires_edinet items (M&A risk) may not skip the EDINET pass: "done"
    # proves the check ran, "unavailable" records honest inaccessibility.
    if input_item.get("requires_edinet") and normalized_checks["edinet"] == "na":
        raise ValueError(
            f"{label}: requires_edinet item must not report checks.edinet=na "
            "(use done, or unavailable when EDINET is inaccessible)"
        )
    if status == "unresolved" and factor_kind != "テーマ":
        raise ValueError(f"{label}: unresolved results must use factor_kind=テーマ")

    return {
        "code": code,
        "status": status,
        "confidence": confidence,
        "factor": factor,
        "factor_kind": factor_kind,
        "claims": claims,
        "sources": sources,
        "checks": normalized_checks,
        "market_note": _required_text(raw.get("market_note"), f"{label}.market_note"),
        "route": input_item.get("route"),
        "risk": input_item.get("risk"),
        "cluster_id": input_item.get("cluster_id"),
    }


def _manifest_codes(manifest: dict[str, Any]) -> list[str]:
    codes = manifest.get("ranking_codes")
    if not isinstance(codes, list):
        raise ValueError("manifest.ranking_codes must be an array")
    normalized = []
    for index, value in enumerate(codes):
        code = _required_text(value, f"manifest.ranking_codes[{index}]")
        normalized.append(code)
    if len(normalized) != len(set(normalized)):
        raise ValueError("manifest.ranking_codes contains duplicates")
    return normalized


def compile_research_results(
    research_dir: str | os.PathLike[str], *, strict: bool = False
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Compile all checkpoint results into evidence v1 and legacy factors.

    Non-strict mode returns valid partial evidence plus structured error text.
    Strict mode raises before callers write either output file.
    """
    root = Path(research_dir)
    manifest = _read_json(root / "manifest.json", "manifest")
    if not isinstance(manifest, dict):
        raise ResearchValidationError(["manifest root must be an object"])
    structural_errors: list[str] = []
    if manifest.get("schema_version") != PLAN_SCHEMA_VERSION:
        structural_errors.append(
            f"manifest.schema_version must be {PLAN_SCHEMA_VERSION}"
        )
    try:
        expected_codes = _manifest_codes(manifest)
    except ValueError as exc:
        structural_errors.append(str(exc))
        expected_codes = []
    batches = manifest.get("batches")
    if not isinstance(batches, list):
        structural_errors.append("manifest.batches must be an array")
        batches = []
    if structural_errors:
        raise ResearchValidationError(structural_errors)

    errors: list[str] = []
    compiled_by_code: dict[str, dict[str, Any]] = {}
    seen_batch_ids: set[str] = set()
    assigned_codes: set[str] = set()

    for batch_index, entry in enumerate(batches):
        batch_label = f"batches[{batch_index}]"
        if not isinstance(entry, dict):
            errors.append(f"{batch_label} must be an object")
            continue
        try:
            batch_id = _required_text(entry.get("batch_id"), f"{batch_label}.batch_id")
            if batch_id in seen_batch_ids:
                raise ValueError(f"duplicate batch_id: {batch_id}")
            seen_batch_ids.add(batch_id)
            input_path = _safe_child(root, entry.get("path"), f"{batch_label}.path")
            result_path = _safe_child(
                root, entry.get("result_path"), f"{batch_label}.result_path"
            )
            batch = _read_json(input_path, f"input batch {batch_id}")
            if not isinstance(batch, dict):
                raise ValueError(f"input batch {batch_id} root must be an object")
            if batch.get("schema_version") != BATCH_SCHEMA_VERSION:
                raise ValueError(
                    f"input batch {batch_id}.schema_version must be {BATCH_SCHEMA_VERSION}"
                )
            if batch.get("batch_id") != batch_id:
                raise ValueError(f"input batch {batch_id} has mismatched batch_id")
            digest = _required_text(entry.get("input_digest"), f"{batch_label}.input_digest")
            if batch.get("input_digest") != digest:
                raise ValueError(f"input batch {batch_id} digest differs from manifest")
            input_items = batch.get("items")
            if not isinstance(input_items, list):
                raise ValueError(f"input batch {batch_id}.items must be an array")
            input_by_code: dict[str, dict[str, Any]] = {}
            for item_index, item in enumerate(input_items):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"input batch {batch_id}.items[{item_index}] must be an object"
                    )
                code = _required_text(
                    item.get("code"), f"input batch {batch_id}.items[{item_index}].code"
                )
                if code in input_by_code or code in assigned_codes:
                    raise ValueError(f"code assigned more than once: {code}")
                input_by_code[code] = item
            assigned_codes.update(input_by_code)

            result = _read_json(result_path, f"result {batch_id}")
            if not isinstance(result, dict):
                raise ValueError(f"result {batch_id} root must be an object")
            if result.get("schema_version") != RESULT_SCHEMA_VERSION:
                raise ValueError(
                    f"result {batch_id}.schema_version must be {RESULT_SCHEMA_VERSION}"
                )
            if result.get("batch_id") != batch_id:
                raise ValueError(f"result {batch_id} has mismatched batch_id")
            if result.get("input_digest") != digest:
                raise ValueError(f"result {batch_id} is stale (input_digest mismatch)")
            result_items = result.get("items")
            if not isinstance(result_items, list):
                raise ValueError(f"result {batch_id}.items must be an array")
        except ValueError as exc:
            errors.append(str(exc))
            continue

        raw_by_code: dict[str, Any] = {}
        for item_index, raw in enumerate(result_items):
            if not isinstance(raw, dict):
                errors.append(f"result {batch_id}.items[{item_index}] must be an object")
                continue
            code = str(raw.get("code") or "").strip()
            if not code:
                errors.append(f"result {batch_id}.items[{item_index}].code is required")
                continue
            if code in raw_by_code:
                errors.append(f"result {batch_id} contains duplicate code: {code}")
                continue
            if code not in input_by_code:
                errors.append(f"result {batch_id} contains unexpected code: {code}")
                continue
            raw_by_code[code] = raw

        for code, input_item in input_by_code.items():
            raw = raw_by_code.get(code)
            if raw is None:
                errors.append(f"result {batch_id} is missing code: {code}")
                continue
            try:
                validated = _validate_result_item(
                    raw, input_item, f"result {batch_id}.{code}"
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if code in compiled_by_code:
                errors.append(f"compiled duplicate code: {code}")
                continue
            compiled_by_code[code] = validated

        # Repair discipline: carry-forward stocks must come back verbatim so a
        # repair pass can never silently rewrite conclusions it was not asked
        # to fix.  Raw items are compared raw-to-raw on the trimmed subset.
        repair_context = batch.get("repair_context")
        if isinstance(repair_context, dict):
            for carry in repair_context.get("carry_forward") or []:
                if not isinstance(carry, dict):
                    continue
                code = str(carry.get("code") or "").strip()
                raw = raw_by_code.get(code)
                if raw is None:
                    continue  # missing-code errors are already reported above
                if trim_carry_item(raw) != trim_carry_item(carry):
                    errors.append(
                        f"result {batch_id}.{code}: repair carry-forward drift "
                        "(carry_forward items must be returned unchanged)"
                    )

    if set(assigned_codes) != set(expected_codes):
        missing_assignments = sorted(set(expected_codes) - assigned_codes)
        unexpected_assignments = sorted(assigned_codes - set(expected_codes))
        if missing_assignments:
            errors.append(f"manifest batches do not assign codes: {missing_assignments}")
        if unexpected_assignments:
            errors.append(f"manifest batches assign unexpected codes: {unexpected_assignments}")

    missing_codes = [code for code in expected_codes if code not in compiled_by_code]
    ordered_items = [compiled_by_code[code] for code in expected_codes if code in compiled_by_code]
    complete = not errors and not missing_codes and len(ordered_items) == len(expected_codes)
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "session_date": manifest.get("session_date"),
        "input_digest": manifest.get("input_digest"),
        "complete": complete,
        "items": ordered_items,
        "missing_codes": missing_codes,
        "errors": errors,
    }
    factors = [
        {
            "code": item["code"],
            "factor": item["factor"],
            "factor_kind": item["factor_kind"],
        }
        for item in ordered_items
    ]
    if strict and not complete:
        details = list(errors)
        if missing_codes:
            details.append(f"missing or invalid codes: {missing_codes}")
        raise ResearchValidationError(details or ["evidence is incomplete"])
    return evidence, factors


def write_compiled_results(
    research_dir: str | os.PathLike[str],
    *,
    evidence_out: str | os.PathLike[str] | None = None,
    factors_out: str | os.PathLike[str] | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    root = Path(research_dir)
    evidence, factors = compile_research_results(root, strict=strict)
    evidence_path = Path(evidence_out) if evidence_out else root / "evidence.json"
    factors_path = Path(factors_out) if factors_out else root / "factors.json"
    _atomic_write_json(evidence_path, evidence)
    _atomic_write_json(factors_path, factors)
    return evidence, factors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile and validate Stage2 batch results into evidence/factors JSON"
    )
    parser.add_argument(
        "--research-dir", required=True, help="Directory containing manifest/batches/results"
    )
    parser.add_argument("--evidence-out", help="Output path (default: research-dir/evidence.json)")
    parser.add_argument("--factors-out", help="Output path (default: research-dir/factors.json)")
    parser.add_argument(
        "--strict", action="store_true", help="Fail without writing if any code is missing/invalid"
    )
    args = parser.parse_args(argv)
    try:
        evidence, _ = write_compiled_results(
            args.research_dir,
            evidence_out=args.evidence_out,
            factors_out=args.factors_out,
            strict=args.strict,
        )
    except (ResearchValidationError, ValueError, OSError) as exc:
        print(f"[compile_research_results] ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(
        "[compile_research_results] OK: "
        f"{len(evidence['items'])} items / complete={str(evidence['complete']).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
