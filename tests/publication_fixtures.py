"""Synthetic complete contracts for publication boundary tests (no network)."""

import json
from pathlib import Path
from tse_ranking_monitor.research.plan import write_research_plan


def research_bundle(ranking, root):
    manifest = write_research_plan(ranking, root)
    rows = {row["code"]: row for row in ranking["rows"]}
    for entry in manifest["batches"]:
        items = []
        for code in entry["codes"]:
            row = rows[code]
            items.append({"code": code, "status": "complete", "confidence": "high",
                          "factor": row["factor"], "factor_kind": row["factor_kind"],
                          "claims": [{"text": "材料を確認", "source_ids": ["s1"]}],
                          "sources": [{"id": "s1", "label": "記事", "url": "https://example.com/article",
                                       "source_type": "article", "window": "material",
                                       "published_at": ranking["session_date"] + "T09:00:00+09:00"}],
                          "checks": {name: "done" for name in ("disclosures", "kabutan_news", "web_search", "sector_cluster", "edinet")},
                          "market_note": "材料を確認"})
        path = Path(root) / entry["result_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "research_batch_result.v1", "batch_id": entry["batch_id"],
                                    "input_digest": entry["input_digest"], "items": items}, ensure_ascii=False), encoding="utf-8")
    return root


def market_document(session):
    return {"schema_version": 1, "session_date": session, "kind": "market_analysis",
            "thesis": "市場動向を確認。", "overview": {}, "theme_matrix": {},
            "market": {"topix_pct": 0.1, "breadth": {"up": 1, "down": 0, "flat": 0}},
            "universe": {"n_liquid": 1, "min_turnover_yen": 100000000},
            "sectors33": [{"code": "0050", "name": "水産・農林業", "w_pct": 0.1,
                           "turnover_oku": 1, "up": 1, "down": 0, "flat": 0, "n": 1}],
            "news_sources": [{"topic": "市場動向", "links": [{"label": "記事", "url": "https://example.com/market"}]}]}
