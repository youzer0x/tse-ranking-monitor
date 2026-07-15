"""Cross-stage data contracts for ranking artifacts."""

from __future__ import annotations

from datetime import date


RANKING_SCHEMA_VERSION = 1
FACTOR_KINDS = {"開示", "報道", "テーマ"}


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_iso_date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_ranking_document(
    data,
    *,
    require_factors=False,
    require_stage1_counts=False,
):
    """Validate the shared Stage1-to-publish ranking contract.

    ``require_factors`` enables the publication boundary checks.  The dropped
    list checks are Stage1-only because older published fixtures did not carry
    those diagnostic arrays.
    """
    if not isinstance(data, dict):
        raise ValueError("root must be an object")

    errors = []
    if data.get("schema_version") != RANKING_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RANKING_SCHEMA_VERSION}")

    session = data.get("session_date")
    if not _valid_iso_date(session):
        errors.append("session_date must be YYYY-MM-DD")

    rows = data.get("rows")
    if not isinstance(rows, list):
        errors.append("rows must be an array")
        rows = []

    seen_codes = set()
    for index, row in enumerate(rows, 1):
        label = f"rows[{index - 1}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        if row.get("rank") != index:
            errors.append(f"{label}.rank must be {index}")
        code = str(row.get("code") or "").strip()
        if not code:
            errors.append(f"{label}.code is required")
        elif code in seen_codes:
            errors.append(f"duplicate code: {code}")
        seen_codes.add(code)
        if require_factors:
            if not str(row.get("factor") or "").strip():
                errors.append(f"{label}.factor is required")
            kind = str(row.get("factor_kind") or "").strip().strip("[]")
            if kind not in FACTOR_KINDS:
                errors.append(
                    f"{label}.factor_kind must be one of {sorted(FACTOR_KINDS)}"
                )

    counts = data.get("counts")
    qualifying = None
    if not isinstance(counts, dict):
        errors.append("counts must be an object")
    else:
        qualifying = counts.get("qualifying")
        ranked = counts.get("ranked")
        if not _is_integer(qualifying):
            errors.append("counts.qualifying must be an integer")
        elif qualifying < len(rows):
            errors.append("counts.qualifying cannot be smaller than rows")
        if not _is_integer(ranked):
            errors.append("counts.ranked must be an integer")
        elif ranked != len(rows):
            errors.append("counts.ranked must equal the number of rows")

    criteria = data.get("criteria")
    maximum = None
    if not isinstance(criteria, dict):
        errors.append("criteria must be an object")
    else:
        maximum = criteria.get("max_rank")
        if maximum is not None and (not _is_integer(maximum) or maximum <= 0):
            errors.append("criteria.max_rank must be a positive integer or null")

    capped = data.get("capped")
    if not isinstance(capped, bool):
        errors.append("capped must be a boolean")
    elif _is_integer(qualifying):
        expected_ranked = min(qualifying, maximum) if maximum else qualifying
        if len(rows) != expected_ranked:
            errors.append(
                "ranked count inconsistent with qualifying/max_rank: "
                f"{len(rows)} != {expected_ranked}"
            )
        if capped != bool(maximum and qualifying > maximum):
            errors.append("capped must reflect whether qualifying exceeds max_rank")

    if require_stage1_counts and isinstance(counts, dict):
        for name in ("dropped_turnover", "dropped_mcap"):
            items = data.get(name)
            if not isinstance(items, list):
                errors.append(f"{name} must be an array")
            elif counts.get(name) != len(items):
                errors.append(f"counts.{name} does not match {name} length")

    if errors:
        raise ValueError("; ".join(errors))
    return data
