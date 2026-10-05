"""Cross-stage data contracts for ranking artifacts."""

from __future__ import annotations

from datetime import date
import math
import re


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
    require_numeric_fields=False,
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
    if require_numeric_fields:
        previous = data.get("prev_date")
        if not _valid_iso_date(previous) or (_valid_iso_date(session) and previous >= session):
            errors.append("prev_date must be an earlier YYYY-MM-DD")

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
        if not _is_integer(row.get("rank")) or row.get("rank") != index:
            errors.append(f"{label}.rank must be {index}")
        code = str(row.get("code") or "").strip()
        if not code:
            errors.append(f"{label}.code is required")
        elif code in seen_codes:
            errors.append(f"duplicate code: {code}")
        seen_codes.add(code)
        if require_numeric_fields:
            if not isinstance(row.get("code"), str) or not re.fullmatch(r"[0-9A-Z]{4,5}", code):
                errors.append(f"{label}.code must be a security code")
            if not isinstance(row.get("name"), str) or not row["name"].strip():
                errors.append(f"{label}.name is required")
            numeric_fields = {"pct": None, "close": 0, "mcap_oku": 0, "turnover_m": 0}
            for field, minimum in numeric_fields.items():
                value = row.get(field)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    errors.append(f"{label}.{field} must be a finite number")
                elif minimum is not None and value <= minimum:
                    errors.append(f"{label}.{field} must be positive")
            for field in ("pct5", "mcap_oku_exact", "turnover_yen", "adj_close", "prev_adj_close"):
                value = row.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    errors.append(f"{label}.{field} must be finite or null")
                elif value is not None and field != "pct5" and value <= 0:
                    errors.append(f"{label}.{field} must be positive")
            valuation = row.get("mcap_date")
            if valuation is not None and (not _valid_iso_date(valuation) or (_valid_iso_date(session) and valuation > session)):
                errors.append(f"{label}.mcap_date must not be after session_date")
            if row.get("mcap_status") not in (None, "current", "previous", "yahoo"):
                errors.append(f"{label}.mcap_status is not publishable")
            c = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
            for field, floor in (("pct", "min_pct"), ("mcap_oku_exact", "min_mcap_oku")):
                value, limit = row.get(field, row.get("mcap_oku") if field == "mcap_oku_exact" else None), c.get(floor)
                if isinstance(value, (int, float)) and isinstance(limit, (int, float)) and value < limit:
                    errors.append(f"{label}.{field} is below {floor}")
            millions = row.get("turnover_m")
            value = row.get("turnover_yen", millions * 1e6 if isinstance(millions, (int, float)) else None)
            if isinstance(value, (int, float)) and isinstance(c.get("min_turnover_yen"), (int, float)) and value < c["min_turnover_yen"]:
                errors.append(f"{label}.turnover is below min_turnover_yen")
        if require_factors:
            if not isinstance(row.get("factor"), str) or not row["factor"].strip():
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
        for field in ("min_pct", "min_mcap_oku", "min_turnover_yen"):
            value = criteria.get(field)
            if require_numeric_fields and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                errors.append(f"criteria.{field} must be a positive finite number")
        maximum = criteria.get("max_rank")
        if maximum is not None and (not _is_integer(maximum) or maximum <= 0):
            errors.append("criteria.max_rank must be a positive integer or null")
            maximum = None

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


def validate_market_numbers(data):
    """Current publication contract. Historical browser reads remain permissive."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("market schema_version must be 1")
    if not _valid_iso_date(data.get("session_date")):
        raise ValueError("market session_date must be YYYY-MM-DD")
    previous = data.get("prev_date")
    if previous is not None and (not _valid_iso_date(previous) or previous >= data["session_date"]):
        raise ValueError("market prev_date must be an earlier date")
    market, universe, sectors = data.get("market"), data.get("universe"), data.get("sectors33")
    if not isinstance(market, dict) or not isinstance(universe, dict) or not isinstance(sectors, list):
        raise ValueError("market/universe must be objects and sectors33 an array")
    errors = []

    def number(value, label, *, count=False, optional=False, nonnegative=False):
        if optional and value is None:
            return
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or (count and not _is_integer(value)) or ((count or nonnegative) and value < 0)):
            errors.append(f"{label} has an invalid numeric value")

    number(market.get("topix_pct"), "market.topix_pct", optional=True)
    number(universe.get("n_liquid"), "universe.n_liquid", count=True)
    breadth = market.get("breadth")
    if not isinstance(breadth, dict):
        errors.append("market.breadth must be an object")
    else:
        for key in ("up", "down", "flat"):
            number(breadth.get(key), "market.breadth." + key, count=True)
        if not errors and sum(breadth[key] for key in ("up", "down", "flat")) != universe["n_liquid"]:
            errors.append("breadth total does not match n_liquid")
    for index, sector in enumerate(sectors):
        if not isinstance(sector, dict) or not isinstance(sector.get("name"), str):
            errors.append(f"sectors33[{index}] must have a name")
            continue
        for key in ("w_pct", "turnover_oku", "up", "down", "flat", "n"):
            number(sector.get(key), f"sectors33[{index}].{key}",
                   count=key in {"up", "down", "flat", "n"}, nonnegative=key == "turnover_oku",
                   optional=key == "w_pct" and sector.get("n") == 0)
        if all(_is_integer(sector.get(key)) for key in ("up", "down", "flat", "n")):
            if sector["up"] + sector["down"] + sector["flat"] != sector["n"]:
                errors.append(f"sectors33[{index}] breadth total does not match n")
    if not errors and sum(sector["n"] for sector in sectors) != universe["n_liquid"]:
        errors.append("sector total does not match n_liquid")
    if errors:
        raise ValueError("; ".join(errors))
    return data
