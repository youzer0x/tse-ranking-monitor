#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the compact, deterministic input used to write the market narrative.

The public ``*_market.json`` schema remains owned by :mod:`.assemble`.  This
module creates a private ``market_brief.v2`` artifact under ``.work`` so the
writer does not need to reread ranking rows, research transcripts, or raw
market statistics: numeric market context, theme clusters, sector drivers,
divergence flags, and compact code-scoped claim/source context from completed
Stage2 findings are all it needs. Individual up/down mover selection and
per-stock narrative reuse were removed in 2026-07-16 along with the
"注目個別銘柄と材料" section (see specs/MARKET_ANALYSIS.md).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys


SCHEMA_VERSION = "market_brief.v2"
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
_SOURCE_KEYS = ("id", "label", "url", "source_type", "published_at", "window")


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


def _scoped_source_id(code, source_id):
    """Namespace an item-local source ID for the flattened brief artifact."""
    return "%s:%s" % (code, source_id)


def _compact_claims(item, code, available_ids):
    """Keep claim text and rewrite its source references to scoped IDs."""
    claims = []
    for claim in item.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or "").strip()
        source_ids = []
        for value in claim.get("source_ids") or []:
            source_id = _code(value)
            if source_id not in available_ids:
                continue
            scoped_id = _scoped_source_id(code, source_id)
            if scoped_id not in source_ids:
                source_ids.append(scoped_id)
        if text or source_ids:
            claims.append({"text": text, "source_ids": source_ids})
    return claims


def _accepted_evidence(evidence_items):
    """Return compact, code-scoped context for cited completed findings.

    Source IDs are local to one Stage2 item, so both the source metadata and
    claim references are namespaced with the stock code before entering this
    all-items artifact.  Only referenced source metadata is retained; article
    bodies and other raw research fields never enter the brief.
    """
    accepted = []
    for item in evidence_items:
        if not _is_complete(item):
            continue
        code = _code(item.get("code"))
        if not code:
            continue

        sources_by_id = {}
        for source in item.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_id = _code(source.get("id"))
            if source_id and source_id not in sources_by_id:
                sources_by_id[source_id] = source

        sources = []
        available_ids = set()
        for source_id in _source_ids(item):
            source = sources_by_id.get(source_id)
            if source is None:
                continue
            compact = {
                key: copy.deepcopy(source[key])
                for key in _SOURCE_KEYS
                if source.get(key) is not None
            }
            compact["id"] = _scoped_source_id(code, source_id)
            sources.append(compact)
            available_ids.add(source_id)

        if not sources:
            continue
        accepted.append({
            "code": code,
            "market_note": str(item.get("market_note") or "").strip(),
            "claims": _compact_claims(item, code, available_ids),
            "sources": sources,
        })
    return accepted


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

    evidence_items = _evidence_items(evidence)

    market = {key: copy.deepcopy(stats[key]) for key in _MARKET_KEYS if stats.get(key) is not None}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "market_brief",
        "session_date": session,
        "market": market,
        "clusters": {
            "theme_clusters": _compact_clusters(ranking),
            "sector_drivers": copy.deepcopy(stats.get("sector_drivers") or {}),
        },
        "divergence_flags": copy.deepcopy(stats.get("divergence_flags") or []),
        "accepted_evidence": _accepted_evidence(evidence_items),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ランキング・evidence・市場statsから compact market_brief.v2 を生成する")
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
    accepted_evidence = brief["accepted_evidence"]
    source_count = sum(len(item["sources"]) for item in accepted_evidence)
    sys.stderr.write(
        "[build_market_brief] OK: %s（clusters %d / evidence %d / sources %d）\n" % (
            args.out,
            len(brief["clusters"]["theme_clusters"]),
            len(accepted_evidence),
            source_count,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
