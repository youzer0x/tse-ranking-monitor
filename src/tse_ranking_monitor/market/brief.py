#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the compact, deterministic input used to write the market narrative.

The public ``*_market.json`` schema remains owned by :mod:`.assemble`.  This
module creates a private ``market_brief.v1`` artifact under ``.work`` so the
writer does not need to reread ranking rows, research transcripts, or raw
market statistics.  In particular, overlapping gainers reuse completed Stage2
evidence verbatim; this module never researches or rewrites a factor.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys


SCHEMA_VERSION = "market_brief.v1"
COMPLETE_STATUSES = {"complete", "completed", "accepted", "done", "ok", "verified"}

_MARKET_KEYS = (
    "prev_date",
    "generated_at",
    "topix_pct",
    "topix_close",
    "breadth",
    "universe",
    "top_sector_by_turnover",
    "top_stock_by_turnover",
)
_MOVER_KEYS = ("code", "name", "rank", "pct", "turnover_m", "turnover_oku", "sector33")
_SOURCE_KEYS = ("id", "label", "url", "source_type", "published_at", "window")
_CONTEXT_KEYS = ("date", "time", "title")


def die(message):
    sys.stderr.write("[build_market_brief] ERROR: %s\n" % message)
    raise SystemExit(1)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        die("JSON 読み込み失敗 %s: %s" % (path, exc))


def _code(value):
    return str(value or "").strip()


def _evidence_items(evidence):
    """Return evidence entries while tolerating early v1 compiler aliases."""
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict)]
    if not isinstance(evidence, dict):
        return []
    for key in ("items", "results", "entries", "rows"):
        value = evidence.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    by_code = evidence.get("by_code")
    if isinstance(by_code, dict):
        out = []
        for code, value in by_code.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("code", code)
                out.append(item)
        return out
    return []


def _is_complete(item):
    status = str(item.get("status") or "").strip().lower().replace("-", "_")
    return status in COMPLETE_STATUSES or item.get("accepted") is True


def _source_ids(item):
    """Collect source IDs in first-seen order without carrying article bodies."""
    out = []

    def add(value):
        source_id = _code(value)
        if source_id and source_id not in out:
            out.append(source_id)

    for value in item.get("source_ids") or []:
        add(value)
    for claim in item.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        for value in claim.get("source_ids") or []:
            add(value)
    # Early evidence writers did not attach source_ids to claims.  Preserve
    # compatibility by falling back to all entry sources only in that case.
    if not out:
        for source in item.get("sources") or []:
            add(source.get("id") if isinstance(source, dict) else source)
    return out


def _source_registry(items, used_ids):
    by_id = {}
    for item in items:
        for source in item.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_id = _code(source.get("id"))
            if not source_id or source_id not in used_ids or source_id in by_id:
                continue
            by_id[source_id] = {
                key: copy.deepcopy(source[key]) for key in _SOURCE_KEYS if source.get(key) is not None
            }
    return [by_id[source_id] for source_id in sorted(by_id)]


def _explicit_movers(stats, side):
    """Read deterministic mover selections when stats v1 (or later) supplies them."""
    candidates = []
    movers = stats.get("movers")
    if isinstance(movers, dict):
        candidates.append(movers.get(side))
    candidates.extend((stats.get("selected_%s" % side), stats.get(side)))
    for value in candidates:
        if isinstance(value, list):
            return value

    # A compact stats producer may annotate movers_context entries with a side.
    selected = []
    for code, value in (stats.get("movers_context") or {}).items():
        if not isinstance(value, dict):
            continue
        marker = str(value.get("side") or value.get("区分") or "").lower()
        wanted = ("loser", "値下がり") if side == "losers" else ("gainer", "値上がり")
        if any(token in marker for token in wanted):
            selected.append({"code": code, **value})
    return selected


def _selected_codes(items):
    out = []
    for item in items:
        code = _code(item.get("code") if isinstance(item, dict) else item)
        if code and code not in out:
            out.append(code)
    return out


def _context_for(stats, code, selected=None):
    raw = None
    if isinstance(selected, dict):
        raw = selected.get("context") or selected.get("disclosures")
    if raw is None:
        raw = (stats.get("movers_context") or {}).get(code)
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("disclosures") or []
    return [
        {key: copy.deepcopy(entry[key]) for key in _CONTEXT_KEYS if entry.get(key) is not None}
        for entry in (raw or []) if isinstance(entry, dict)
    ]


def _compact_clusters(ranking):
    clusters = []
    for cluster in ranking.get("theme_clusters") or []:
        if not isinstance(cluster, dict):
            continue
        item = {}
        for key in ("id", "cluster_id", "sec33", "name", "size", "leader_code", "leader_basis"):
            if cluster.get(key) is not None:
                item[key] = copy.deepcopy(cluster[key])
        members = cluster.get("members") or cluster.get("codes")
        if isinstance(members, list):
            item["members"] = [_code(code) for code in members if _code(code)]
        clusters.append(item)
    return clusters


def build_market_brief(ranking, evidence, stats):
    """Create a compact market brief from three already-produced artifacts."""
    if not all(isinstance(value, dict) for value in (ranking, evidence, stats)):
        raise ValueError("ranking, evidence, and stats must be JSON objects")

    session = ranking.get("session_date")
    if not session:
        raise ValueError("ranking.session_date is required")
    for label, doc in (("evidence", evidence), ("stats", stats)):
        other = doc.get("session_date")
        if other and other != session:
            raise ValueError("%s.session_date(%s) != ranking.session_date(%s)" % (label, other, session))

    ranking_rows = [row for row in ranking.get("rows") or [] if isinstance(row, dict)]
    rows_by_code = {_code(row.get("code")): row for row in ranking_rows if _code(row.get("code"))}
    evidence_items = _evidence_items(evidence)
    evidence_by_code = {
        _code(item.get("code")): item for item in evidence_items if _code(item.get("code"))
    }

    explicit_gainers = _explicit_movers(stats, "gainers")
    gainer_codes = _selected_codes(explicit_gainers) or list(rows_by_code)
    gainers = []
    used_source_ids = set()
    for code in gainer_codes:
        row = rows_by_code.get(code)
        item = evidence_by_code.get(code)
        if row is None or item is None or not _is_complete(item):
            continue
        source_ids = _source_ids(item)
        used_source_ids.update(source_ids)
        mover = {key: copy.deepcopy(row[key]) for key in _MOVER_KEYS if row.get(key) is not None}
        mover["code"] = code
        mover["factor"] = item.get("factor") or row.get("factor") or ""
        mover["factor_kind"] = item.get("factor_kind") or row.get("factor_kind") or ""
        mover["market_note"] = item.get("market_note") or mover["factor"]
        mover["source_ids"] = source_ids
        gainers.append(mover)

    selected_losers = _explicit_movers(stats, "losers")
    losers = []
    for selected in selected_losers:
        code = _code(selected.get("code") if isinstance(selected, dict) else selected)
        if not code:
            continue
        loser = {"code": code}
        if isinstance(selected, dict):
            for key in _MOVER_KEYS:
                if selected.get(key) is not None:
                    loser[key] = copy.deepcopy(selected[key])
            if selected.get("note") is not None:
                loser["note"] = selected["note"]
            ids = [_code(value) for value in selected.get("source_ids") or [] if _code(value)]
            if ids:
                loser["source_ids"] = ids
                used_source_ids.update(ids)
        loser["context"] = _context_for(stats, code, selected if isinstance(selected, dict) else None)
        losers.append(loser)

    market = {key: copy.deepcopy(stats[key]) for key in _MARKET_KEYS if stats.get(key) is not None}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "market_brief",
        "session_date": session,
        "market": market,
        "movers": {"gainers": gainers, "losers": losers},
        "clusters": {
            "theme_clusters": _compact_clusters(ranking),
            "sector_drivers": copy.deepcopy(stats.get("sector_drivers") or {}),
        },
        "divergence_flags": copy.deepcopy(stats.get("divergence_flags") or []),
        "sources": _source_registry(evidence_items, used_source_ids),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ランキング・evidence・市場statsから compact market_brief.v1 を生成する")
    parser.add_argument("--ranking", required=True, help="Stage2反映済み ranking.json")
    parser.add_argument("--evidence", required=True, help="research/evidence.json (evidence.v1)")
    parser.add_argument("--stats", required=True, help="market_stats_<date>.json")
    parser.add_argument("--out", required=True, help="非公開出力 .work/<SESSION>/market/market_brief_<date>.json")
    args = parser.parse_args(argv)

    try:
        brief = build_market_brief(
            load_json(args.ranking), load_json(args.evidence), load_json(args.stats))
    except ValueError as exc:
        die(str(exc))
    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(brief, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    sys.stderr.write("[build_market_brief] OK: %s（gainers %d / losers %d / sources %d）\n" % (
        args.out, len(brief["movers"]["gainers"]), len(brief["movers"]["losers"]),
        len(brief["sources"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
