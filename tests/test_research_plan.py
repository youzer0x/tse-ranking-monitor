"""Compact Stage2 research-plan projection tests."""

import json

import pytest

from tse_ranking_monitor.research.plan import (
    BATCH_SCHEMA_VERSION,
    build_research_plan,
    write_research_plan,
)


def _row(code, rank, **overrides):
    row = {
        "code": code,
        "name": f"Company {code}",
        "rank": rank,
        "pct": 8.0,
        "pct5": 3.0,
        "turnover_m": 500.0,
        "market": "プライム",
        "mcap_oku": 999,
        "close": 1234,
        "factor": "",
        "factor_kind": "",
        "disclosures": [],
        "kabutan_news": [],
    }
    row.update(overrides)
    return row


def _ranking(rows):
    return {
        "session_date": "2026-07-15",
        "prev_date": "2026-07-14",
        "rows": rows,
        "theme_clusters": [],
    }


def test_plan_projects_research_fields_and_classifies_news():
    news = [
        {
            "datetime": "2026-07-15T15:30:00+09:00",
            "category": "材料",
            "title": "引け後記事",
            "url": "https://kabutan.jp/news/after-close",
        },
        {
            "datetime": "2026-07-15T15:29:59+09:00",
            "category": "材料",
            "title": "場中記事",
            "url": "https://kabutan.jp/news/material",
        },
        {
            "datetime": "2026-07-15T10:00:00+09:00",
            "category": "開示",
            "title": "TDnet転載",
            "url": "https://kabutan.jp/disclosures/pdf/example/",
        },
        {
            "datetime": "2026-07-14T15:29:00+09:00",
            "category": "材料",
            "title": "直前記事1",
            "url": "https://kabutan.jp/news/prior-1",
        },
        {
            "datetime": "2026-07-14T14:00:00+09:00",
            "category": "材料",
            "title": "直前記事2",
            "url": "https://kabutan.jp/news/prior-2",
        },
        {
            "datetime": "2026-07-13T14:00:00+09:00",
            "category": "材料",
            "title": "古い記事",
            "url": "https://kabutan.jp/news/prior-3",
        },
    ]
    ranking = _ranking([_row("1001", 1, kabutan_news=news)])

    manifest, batches = build_research_plan(ranking)

    assert manifest["stats"]["news"] == {
        "duplicates_omitted": 0,
        "material_window": 1,
        "post_close_omitted": 1,
        "prior": 3,
        "prior_omitted_by_cap": 1,
        "prior_retained": 2,
        "tdnet_duplicates_omitted": 1,
    }
    assert len(batches) == 1
    item = batches[0]["items"][0]
    assert set(item) == {
        "code",
        "name",
        "rank",
        "pct",
        "pct5",
        "turnover_m",
        "route",
        "risk",
        "risk_reasons",
        "cluster_id",
        "disclosures",
        "news",
    }
    assert batches[0]["checks_required"] == [
        "disclosures",
        "kabutan_news",
        "web_search",
        "sector_cluster",
        "edinet",
    ]
    assert item["route"] == "news"
    assert [entry["title"] for entry in item["news"]["material_window"]] == [
        "場中記事"
    ]
    assert [entry["title"] for entry in item["news"]["prior"]] == [
        "直前記事1",
        "直前記事2",
    ]
    serialized = json.dumps(batches[0], ensure_ascii=False)
    assert "引け後記事" not in serialized
    assert "TDnet転載" not in serialized
    assert "market" not in item
    assert "mcap_oku" not in item
    assert "close" not in item


def test_plan_normalizes_clusters_and_routes_deterministically():
    peer_a = {
        "code": "2001",
        "name": "A",
        "rank": 1,
        "pct": 10.0,
        "turnover_m": 1000.0,
        "has_disclosure": True,
    }
    peer_b = {
        "code": "2002",
        "name": "B",
        "rank": 2,
        "pct": 9.0,
        "turnover_m": 900.0,
        "has_disclosure": False,
    }
    repeated_cluster = {
        "sec33": "5250",
        "name": "情報・通信業",
        "size": 2,
        "peers": [peer_a, peer_b],
        "leader_code": "2001",
        "leader_basis": "disclosure",
    }
    rows = [
        _row("2001", 1, sector_cluster=repeated_cluster),
        _row("2002", 2, sector_cluster=repeated_cluster),
        _row(
            "2003",
            3,
            disclosures=[{
                "date": "2026-07-14",
                "time": "15:30",
                "title": "上方修正",
                "pdf_url": "https://example.com/disclosure.pdf",
            }],
        ),
        _row("2004", 4),
    ]
    ranking = _ranking(rows)
    ranking["theme_clusters"] = [{
        "sec33": "5250",
        "name": "情報・通信業",
        "members": ["2001", "2002"],
        "leader_code": "2001",
        "leader_basis": "disclosure",
    }]

    manifest1, batches1 = build_research_plan(ranking)
    manifest2, batches2 = build_research_plan(ranking)

    assert manifest1 == manifest2
    assert batches1 == batches2
    assert len(manifest1["clusters"]) == 1
    cluster = manifest1["clusters"][0]
    assert cluster["id"] == "s33:5250"
    assert [member["code"] for member in cluster["members"]] == ["2001", "2002"]
    assert cluster["members"][0]["has_disclosure"] is False  # ranking row is authoritative
    items = {item["code"]: item for batch in batches1 for item in batch["items"]}
    assert items["2001"]["route"] == "cluster"
    assert items["2002"]["route"] == "cluster"
    assert items["2003"]["route"] == "disclosure"
    assert items["2004"]["route"] == "deep"
    assert sum(len(batch["items"]) for batch in batches1) == 4
    assert all(len(batch["items"]) <= 5 for batch in batches1)
    assert all(
        len(batch["items"]) <= 3
        for batch in batches1
        if batch["route"] == "deep" or batch["risk"] == "high"
    )


def test_m_and_a_is_solo_and_out_of_window_disclosure_is_omitted():
    rows = [
        _row(
            "3001",
            1,
            disclosures=[{
                "date": "2026-07-15",
                "time": "15:30",
                "title": "公開買付けに関するお知らせ",
                "pdf_url": "https://example.com/late.pdf",
            }],
            kabutan_news=[{
                "datetime": "2026-07-15T10:00:00+09:00",
                "category": "材料",
                "title": "TOB観測",
                "url": "https://example.com/tob",
            }],
        ),
        _row("3002", 2),
    ]
    manifest, batches = build_research_plan(_ranking(rows))
    target = next(batch for batch in batches if batch["items"][0]["code"] == "3001")
    assert len(target["items"]) == 1
    assert target["risk"] == "high"
    assert target["route"] == "news"
    assert target["items"][0]["disclosures"] == []
    assert manifest["stats"]["disclosures_omitted_outside_window_or_duplicate"] == 1


def test_write_plan_emits_batch_files_and_reuses_valid_checkpoint(tmp_path):
    research_dir = tmp_path / "research"
    ranking = _ranking([_row("4001", 1), _row("4002", 2)])
    first = write_research_plan(ranking, research_dir)
    entry = first["batches"][0]
    batch_path = research_dir / entry["path"]
    assert batch_path.exists()
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    assert batch["schema_version"] == BATCH_SCHEMA_VERSION
    assert batch["input_digest"] == entry["input_digest"]
    assert batch_path.stat().st_size == entry["input_bytes"]
    assert first["stats"]["batch_input_bytes_total"] == sum(
        batch_entry["input_bytes"] for batch_entry in first["batches"]
    )
    assert first["stats"]["batch_input_bytes_max"] == max(
        batch_entry["input_bytes"] for batch_entry in first["batches"]
    )

    result_path = research_dir / entry["result_path"]
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps({
            "schema_version": "research_batch_result.v1",
            "batch_id": entry["batch_id"],
            "input_digest": entry["input_digest"],
            "items": [{
                "code": code,
                "status": "complete",
                "confidence": "medium",
                "factor": "テーマ物色と並走したとみられる。",
                "factor_kind": "テーマ",
                "claims": [{"text": "テーマ物色", "source_ids": []}],
                "sources": [],
                "checks": {
                    "disclosures": "done",
                    "kabutan_news": "done",
                    "web_search": "done",
                    "sector_cluster": "na",
                    "edinet": "na",
                },
                "market_note": "テーマ物色と並走。",
            } for code in entry["codes"]],
        }),
        encoding="utf-8",
    )
    second = write_research_plan(ranking, research_dir)
    assert second["batches"][0]["status"] == "complete"

    ranking["rows"][0]["pct"] = 9.0
    third = write_research_plan(ranking, research_dir)
    assert third["batches"][0]["status"] == "invalid"


def test_plan_rejects_duplicate_codes_and_missing_prev_date():
    with pytest.raises(ValueError, match="duplicate ranking code"):
        build_research_plan(_ranking([_row("5001", 1), _row("5001", 2)]))
    ranking = _ranking([_row("5001", 1)])
    del ranking["prev_date"]
    with pytest.raises(ValueError, match="prev_date"):
        build_research_plan(ranking)
