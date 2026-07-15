"""Build compact, deterministic Stage2 research batches from ranking JSON.

The public ranking document is read-only.  This module projects only fields
needed for factor research, normalizes repeated sector clusters, trims old
Kabutan history, and writes one self-contained JSON file per agent batch.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


PLAN_SCHEMA_VERSION = "research_plan.v1"
BATCH_SCHEMA_VERSION = "research_batch.v1"
RESULT_SCHEMA_VERSION = "research_batch_result.v1"

JST = timezone(timedelta(hours=9))
CHECK_NAMES = (
    "disclosures",
    "kabutan_news",
    "web_search",
    "sector_cluster",
    "edinet",
)
ROUTE_ORDER = {"disclosure": 0, "news": 1, "cluster": 2, "deep": 3}
M_AND_A_TERMS = (
    "TOB",
    "MBO",
    "公開買付",
    "買収",
    "非公開化",
    "完全子会社",
)


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compact_json_size(value: Any) -> int:
    """Return the exact UTF-8 byte count written for a compact JSON batch."""
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return len(raw) + 1  # trailing newline from _atomic_write_json


def _atomic_write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        if compact:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _iso_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be YYYY-MM-DD")
    return parsed


def _news_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.datetime is required")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label}.datetime must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def _window(ranking: dict[str, Any]) -> tuple[datetime, datetime]:
    session = _iso_date(ranking.get("session_date"), "session_date")
    previous = _iso_date(ranking.get("prev_date"), "prev_date")
    start = datetime.combine(previous, time(15, 30), JST)
    end = datetime.combine(session, time(15, 30), JST)
    if start >= end:
        raise ValueError("prev_date must precede session_date")
    return start, end


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(row.get("code") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "rank": row.get("rank"),
        "pct": row.get("pct"),
        "turnover_m": row.get("turnover_m"),
        "has_disclosure": bool(row.get("disclosures")),
    }


def _normalize_clusters(rows: list[dict[str, Any]], raw_clusters: Any) -> list[dict[str, Any]]:
    """Collapse the per-row repeated cluster objects into one registry."""
    clusters: dict[str, dict[str, Any]] = {}
    members: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def ensure(sec33: Any, source: dict[str, Any]) -> dict[str, Any] | None:
        key = str(sec33 or "").strip()
        if not key:
            return None
        cluster = clusters.setdefault(
            key,
            {
                "id": f"s33:{key}",
                "sector_code": key,
                "name": str(source.get("name") or "").strip(),
                "leader_code": str(source.get("leader_code") or "").strip() or None,
                "leader_basis": str(source.get("leader_basis") or "").strip() or None,
            },
        )
        if not cluster["name"]:
            cluster["name"] = str(source.get("name") or "").strip()
        if not cluster["leader_code"]:
            cluster["leader_code"] = str(source.get("leader_code") or "").strip() or None
        if not cluster["leader_basis"]:
            cluster["leader_basis"] = str(source.get("leader_basis") or "").strip() or None
        return cluster

    if raw_clusters is None:
        raw_clusters = []
    if not isinstance(raw_clusters, list):
        raise ValueError("theme_clusters must be an array")
    for index, source in enumerate(raw_clusters):
        if not isinstance(source, dict):
            raise ValueError(f"theme_clusters[{index}] must be an object")
        cluster = ensure(source.get("sec33"), source)
        if cluster is None:
            continue
        key = cluster["sector_code"]
        for raw_code in source.get("members") or []:
            code = str(raw_code or "").strip()
            if code:
                members[key].setdefault(code, {"code": code})

    for row in rows:
        source = row.get("sector_cluster")
        if not isinstance(source, dict):
            continue
        cluster = ensure(source.get("sec33") or row.get("sec33"), source)
        if cluster is None:
            continue
        key = cluster["sector_code"]
        summary = _row_summary(row)
        if summary["code"]:
            members[key][summary["code"]] = summary
        peers = source.get("peers") or []
        if not isinstance(peers, list):
            raise ValueError(f"sector_cluster.peers for {summary['code']} must be an array")
        for peer in peers:
            if not isinstance(peer, dict):
                continue
            peer_summary = _row_summary(peer)
            if peer_summary["code"]:
                members[key][peer_summary["code"]] = peer_summary

    row_by_code = {
        str(row.get("code") or "").strip(): row
        for row in rows
        if str(row.get("code") or "").strip()
    }
    for key, registry in members.items():
        for code in list(registry):
            if code in row_by_code:
                registry[code] = _row_summary(row_by_code[code])

    output = []
    for key in sorted(clusters):
        cluster_members = list(members[key].values())
        cluster_members.sort(
            key=lambda item: (
                item.get("rank") if isinstance(item.get("rank"), int) else 10**9,
                item["code"],
            )
        )
        if len(cluster_members) < 2:
            continue
        output.append(
            {
                **clusters[key],
                # Rank/return/turnover stay on assigned items.  Cluster context
                # needs only identity plus disclosure presence; dropping the
                # repeated numeric fields materially reduces multi-batch input.
                "members": [
                    {
                        "code": member["code"],
                        "name": member.get("name", ""),
                        "has_disclosure": bool(member.get("has_disclosure")),
                    }
                    for member in cluster_members
                ],
            }
        )
    return output


def _compact_disclosures(
    row: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], int]:
    disclosures = row.get("disclosures") or []
    if not isinstance(disclosures, list):
        raise ValueError(f"row {row.get('code')}: disclosures must be an array")
    kept: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    omitted = 0
    for index, item in enumerate(disclosures):
        if not isinstance(item, dict):
            raise ValueError(f"row {row.get('code')}: disclosures[{index}] must be an object")
        raw_date = item.get("date")
        raw_time = item.get("time")
        if not isinstance(raw_time, str):
            raise ValueError(f"row {row.get('code')}: disclosure time is required")
        try:
            published = datetime.combine(
                _iso_date(raw_date, "disclosure.date"),
                time.fromisoformat(raw_time),
                JST,
            )
        except ValueError as exc:
            raise ValueError(f"row {row.get('code')}: invalid disclosure datetime") from exc
        if not (start <= published < end):
            omitted += 1
            continue
        compact = {
            "date": published.date().isoformat(),
            "time": published.strftime("%H:%M"),
            "title": str(item.get("title") or "").strip(),
            "pdf_url": str(item.get("pdf_url") or "").strip(),
        }
        identity = (
            compact["pdf_url"] or None,
            compact["date"],
            compact["time"],
            compact["title"],
        )
        if identity in seen:
            omitted += 1
            continue
        seen.add(identity)
        kept.append(compact)
    kept.sort(key=lambda item: (item["date"], item["time"], item["title"]))
    return kept, omitted


def _compact_news(
    row: dict[str, Any], start: datetime, end: datetime
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    raw_news = row.get("kabutan_news") or []
    if not isinstance(raw_news, list):
        raise ValueError(f"row {row.get('code')}: kabutan_news must be an array")
    classified: dict[str, list[tuple[datetime, dict[str, Any]]]] = {
        "material_window": [],
        "prior": [],
    }
    counts = {
        "material_window": 0,
        "prior": 0,
        "post_close_omitted": 0,
        "tdnet_duplicates_omitted": 0,
        "duplicates_omitted": 0,
    }
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw_news):
        if not isinstance(item, dict):
            raise ValueError(f"row {row.get('code')}: kabutan_news[{index}] must be an object")
        published = _news_datetime(item.get("datetime"), f"kabutan_news[{index}]")
        category = str(item.get("category") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        identity = (url, published.isoformat(timespec="seconds"), title)
        if identity in seen:
            counts["duplicates_omitted"] += 1
            continue
        seen.add(identity)

        # Kabutan's 開示 category mirrors TDnet and otherwise repeats the
        # authoritative disclosure payload already attached to the row.
        if category == "開示" or "/disclosures/" in url:
            counts["tdnet_duplicates_omitted"] += 1
            continue
        if published >= end:
            counts["post_close_omitted"] += 1
            continue
        bucket = "material_window" if published >= start else "prior"
        compact = {
            "published_at": published.isoformat(timespec="seconds"),
            "category": category,
            "title": title,
            "url": url,
        }
        classified[bucket].append((published, compact))
        counts[bucket] += 1

    for bucket in classified:
        classified[bucket].sort(key=lambda pair: pair[0], reverse=True)
    # Two prior headlines provide continuation context without forwarding the
    # dozens of stale entries often present in the Stage1 payload.
    prior = [item for _, item in classified["prior"][:2]]
    counts["prior_retained"] = len(prior)
    counts["prior_omitted_by_cap"] = max(0, len(classified["prior"]) - len(prior))
    return {
        "material_window": [item for _, item in classified["material_window"]],
        "prior": prior,
    }, counts


def _risk_reasons(row: dict[str, Any], text: str) -> list[str]:
    reasons = []
    if any(term.casefold() in text.casefold() for term in M_AND_A_TERMS):
        reasons.append("m_and_a")
    pct = row.get("pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct >= 15:
        reasons.append("large_move")
    turnover = row.get("turnover_m")
    if (
        isinstance(turnover, (int, float))
        and not isinstance(turnover, bool)
        and turnover >= 10_000
    ):
        reasons.append("high_turnover")
    return reasons


def _checkpoint_status(path: Path, batch: dict[str, Any]) -> str:
    if not path.exists():
        return "pending"
    try:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(result, dict):
        return "invalid"
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        return "invalid"
    if result.get("batch_id") != batch["batch_id"]:
        return "invalid"
    if result.get("input_digest") != batch["input_digest"]:
        return "invalid"
    items = result.get("items")
    if not isinstance(items, list):
        return "invalid"
    required_item_keys = {
        "code",
        "status",
        "confidence",
        "factor",
        "factor_kind",
        "claims",
        "sources",
        "checks",
        "market_note",
    }
    for item in items:
        if not isinstance(item, dict) or not required_item_keys.issubset(item):
            return "invalid"
        if not isinstance(item.get("factor"), str) or not item["factor"].strip():
            return "invalid"
        if not isinstance(item.get("market_note"), str) or not item["market_note"].strip():
            return "invalid"
        if not isinstance(item.get("claims"), list) or not item["claims"]:
            return "invalid"
        if not isinstance(item.get("sources"), list):
            return "invalid"
        if not isinstance(item.get("checks"), dict) or set(item["checks"]) != set(CHECK_NAMES):
            return "invalid"
    codes = [str(item.get("code") or "").strip() for item in items]
    expected = [item["code"] for item in batch["items"]]
    return "complete" if len(codes) == len(set(codes)) and set(codes) == set(expected) else "invalid"


def build_research_plan(ranking: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a research-plan manifest and self-contained agent batches."""
    if not isinstance(ranking, dict):
        raise ValueError("ranking root must be an object")
    rows = ranking.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ranking.rows must be an array")
    start, end = _window(ranking)

    codes: list[str] = []
    seen_codes: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"rows[{index}] must be an object")
        code = str(row.get("code") or "").strip()
        if not code:
            raise ValueError(f"rows[{index}].code is required")
        if code in seen_codes:
            raise ValueError(f"duplicate ranking code: {code}")
        seen_codes.add(code)
        codes.append(code)

    clusters = _normalize_clusters(rows, ranking.get("theme_clusters", []))
    cluster_by_code: dict[str, str] = {}
    for cluster in clusters:
        for member in cluster["members"]:
            cluster_by_code[member["code"]] = cluster["id"]

    news_totals: dict[str, int] = defaultdict(int)
    disclosure_omitted = 0
    projected: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["code"]).strip()
        disclosures, omitted = _compact_disclosures(row, start, end)
        disclosure_omitted += omitted
        news, news_counts = _compact_news(row, start, end)
        for key, value in news_counts.items():
            news_totals[key] += value
        context_text = " ".join(
            [item["title"] for item in disclosures]
            + [item["title"] for item in news["material_window"]]
        )
        reasons = _risk_reasons(row, context_text)
        cluster_id = cluster_by_code.get(code)
        if disclosures:
            route = "disclosure"
        elif news["material_window"]:
            route = "news"
        elif cluster_id:
            route = "cluster"
        else:
            route = "deep"
        projected.append(
            {
                "code": code,
                "name": str(row.get("name") or "").strip(),
                "rank": row.get("rank"),
                "pct": row.get("pct"),
                "pct5": row.get("pct5"),
                "turnover_m": row.get("turnover_m"),
                "route": route,
                "risk": "high" if reasons else "normal",
                "risk_reasons": reasons,
                "cluster_id": cluster_id,
                "disclosures": disclosures,
                "news": news,
            }
        )

    batch_payloads: list[dict[str, Any]] = []
    cluster_index = {cluster["id"]: cluster for cluster in clusters}
    batch_number = 0

    def append_batch(chunk: list[dict[str, Any]]) -> None:
        nonlocal batch_number
        if not chunk:
            return
        batch_number += 1
        risk = "high" if any(item["risk"] == "high" for item in chunk) else "normal"
        item_routes = {item["route"] for item in chunk}
        route = next(iter(item_routes)) if len(item_routes) == 1 else "mixed"
        cluster_ids = sorted({item["cluster_id"] for item in chunk if item["cluster_id"]})
        payload = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "batch_id": f"batch-{batch_number:03d}",
            "session_date": ranking["session_date"],
            "prev_date": ranking["prev_date"],
            "window": {
                "start": start.isoformat(timespec="seconds"),
                "end_exclusive": end.isoformat(timespec="seconds"),
            },
            "route": route,
            "risk": risk,
            "checks_required": list(CHECK_NAMES),
            "clusters": [cluster_index[cluster_id] for cluster_id in cluster_ids],
            "items": chunk,
        }
        payload["input_digest"] = _canonical_digest(payload)
        batch_payloads.append(payload)

    def context_key(item: dict[str, Any]) -> tuple[Any, ...]:
        material = item["news"]["material_window"]
        shared_url = material[0]["url"] if material else ""
        return (
            ROUTE_ORDER[item["route"]],
            item.get("cluster_id") or "",
            shared_url,
            item.get("rank") or 10**9,
            item["code"],
        )

    # M&A evidence warrants an isolated task.  High-risk rows are pooled into
    # max-three batches across routes; normal deep-search rows also stay at
    # max three.  Remaining routes share max-five batches, allowing a partial
    # route tail to use the next route's spare capacity without duplicating
    # context (the exact route remains on every item).
    solo_items = sorted(
        [item for item in projected if "m_and_a" in item["risk_reasons"]],
        key=context_key,
    )
    remaining = [item for item in projected if "m_and_a" not in item["risk_reasons"]]
    high = sorted([item for item in remaining if item["risk"] == "high"], key=context_key)
    normal_deep = sorted(
        [item for item in remaining if item["risk"] == "normal" and item["route"] == "deep"],
        key=context_key,
    )
    normal_direct = sorted(
        [item for item in remaining if item["risk"] == "normal" and item["route"] != "deep"],
        key=context_key,
    )
    for item in solo_items:
        append_batch([item])
    for items, limit in ((high, 3), (normal_direct, 5), (normal_deep, 3)):
        for offset in range(0, len(items), limit):
            append_batch(items[offset : offset + limit])

    manifest_batches = [
        {
            "batch_id": batch["batch_id"],
            "path": f"batches/{batch['batch_id']}.json",
            "result_path": f"results/{batch['batch_id']}.json",
            "input_digest": batch["input_digest"],
            "input_bytes": _compact_json_size(batch),
            "status": "pending",
            "codes": [item["code"] for item in batch["items"]],
            "route": batch["route"],
            "risk": batch["risk"],
        }
        for batch in batch_payloads
    ]
    batch_input_sizes = [entry["input_bytes"] for entry in manifest_batches]
    manifest = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "session_date": ranking["session_date"],
        "prev_date": ranking["prev_date"],
        "window": {
            "start": start.isoformat(timespec="seconds"),
            "end_exclusive": end.isoformat(timespec="seconds"),
        },
        "input_digest": _canonical_digest(
            {
                "session_date": ranking["session_date"],
                "codes": codes,
                "batches": [batch["input_digest"] for batch in batch_payloads],
            }
        ),
        "ranking_codes": codes,
        "clusters": clusters,
        "batches": manifest_batches,
        "stats": {
            "rows": len(rows),
            "clusters": len(clusters),
            "batches": len(batch_payloads),
            "batch_input_bytes_total": sum(batch_input_sizes),
            "batch_input_bytes_max": max(batch_input_sizes, default=0),
            "news": dict(sorted(news_totals.items())),
            "disclosures_omitted_outside_window_or_duplicate": disclosure_omitted,
        },
    }
    return manifest, batch_payloads


def write_research_plan(ranking: dict[str, Any], out_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Atomically write batches and manifest, preserving result checkpoints."""
    destination = Path(out_dir)
    manifest, batches = build_research_plan(ranking)
    for batch in batches:
        _atomic_write_json(
            destination / "batches" / f"{batch['batch_id']}.json",
            batch,
            compact=True,
        )
    batch_by_id = {batch["batch_id"]: batch for batch in batches}
    for entry in manifest["batches"]:
        result_path = destination / entry["result_path"]
        entry["status"] = _checkpoint_status(result_path, batch_by_id[entry["batch_id"]])
    _atomic_write_json(destination / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build compact Stage2 research batches from ranking JSON"
    )
    parser.add_argument("--ranking", required=True, help="Stage1 ranking JSON")
    parser.add_argument(
        "--out-dir", required=True, help="Research directory for manifest/batches/results"
    )
    args = parser.parse_args(argv)
    try:
        with open(args.ranking, encoding="utf-8") as handle:
            ranking = json.load(handle)
        manifest = write_research_plan(ranking, args.out_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[build_research_plan] ERROR: {exc}", file=os.sys.stderr)
        return 1
    counts = {status: 0 for status in ("pending", "complete", "invalid")}
    for batch in manifest["batches"]:
        counts[batch["status"]] += 1
    print(
        "[build_research_plan] OK: "
        f"{manifest['stats']['rows']} rows / {manifest['stats']['batches']} batches "
        f"(complete={counts['complete']}, pending={counts['pending']}, invalid={counts['invalid']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
