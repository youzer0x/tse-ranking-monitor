"""Compact Stage2 research-plan projection tests."""

import json

import pytest

from tse_ranking_monitor.research.plan import (
    BATCH_SCHEMA_VERSION,
    INITIAL_DISPATCH_LIMIT,
    build_research_plan,
    main as plan_main,
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


def _tob_news(code):
    """In-window TOB headline -> m_and_a risk reason, route news."""
    return [{
        "datetime": "2026-07-15T10:00:00+09:00",
        "category": "材料",
        "title": "TOB観測で急伸",
        "url": f"https://kabutan.jp/news/tob-{code}",
    }]


def _high_row(code, rank):
    """large_move (pct>=15) with no disclosures/news/cluster -> high risk, route deep."""
    return _row(code, rank, pct=20.0)


def _direct_row(code, rank):
    """In-window disclosure -> normal risk, route disclosure (normal_direct pool)."""
    return _row(code, rank, disclosures=[{
        "date": "2026-07-14",
        "time": "15:30",
        "title": "上方修正",
        "pdf_url": f"https://example.com/{code}.pdf",
    }])


def _ranking(rows):
    return {
        "session_date": "2026-07-15",
        "prev_date": "2026-07-14",
        "rows": rows,
        "theme_clusters": [],
    }


def _valid_result(entry):
    return {
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
    }


def _write_result(research_dir, entry, result=None):
    path = research_dir / entry["result_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result if result is not None else _valid_result(entry), ensure_ascii=False),
        encoding="utf-8",
    )


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


def test_m_and_a_pools_with_high_risk_and_out_of_window_disclosure_is_omitted():
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
            kabutan_news=_tob_news("3001"),
        ),
        _high_row("3002", 2),
        _high_row("3003", 3),
    ]
    manifest, batches = build_research_plan(_ranking(rows))
    assert len(batches) == 1  # M&A no longer forces a solo batch
    batch = batches[0]
    assert batch["risk"] == "high"
    assert sorted(item["code"] for item in batch["items"]) == ["3001", "3002", "3003"]
    items = {item["code"]: item for item in batch["items"]}
    assert items["3001"]["risk_reasons"] == ["m_and_a"]
    assert items["3001"]["requires_edinet"] is True
    assert "requires_edinet" not in items["3002"]
    assert items["3001"]["disclosures"] == []  # 15:30 disclosure is out of window
    assert manifest["stats"]["disclosures_omitted_outside_window_or_duplicate"] == 1


def test_four_m_and_a_rows_pool_into_two_high_batches():
    rows = [
        _row(f"310{index}", index, kabutan_news=_tob_news(f"310{index}"))
        for index in range(1, 5)
    ]
    _, batches = build_research_plan(_ranking(rows))
    assert [len(batch["items"]) for batch in batches] == [3, 1]
    assert all(batch["risk"] == "high" for batch in batches)
    assert all(
        item["requires_edinet"] is True
        for batch in batches
        for item in batch["items"]
    )


def test_batch_count_never_exceeds_initial_limit_for_any_30_row_mix():
    """Exhaustive bound proof over every (high, deep, direct) split of 30 rows."""
    counts = {}
    for high_count in range(31):
        for deep_count in range(31 - high_count):
            direct_count = 30 - high_count - deep_count
            rows = []
            rank = 0
            for _ in range(high_count):
                rank += 1
                rows.append(_high_row(f"{1000 + rank}", rank))
            for _ in range(deep_count):
                rank += 1
                rows.append(_row(f"{1000 + rank}", rank))
            for _ in range(direct_count):
                rank += 1
                rows.append(_direct_row(f"{1000 + rank}", rank))
            _, batches = build_research_plan(_ranking(rows))
            key = (high_count, deep_count, direct_count)
            counts[key] = len(batches)
            assert counts[key] <= INITIAL_DISPATCH_LIMIT, key
    assert counts[(1, 28, 1)] == INITIAL_DISPATCH_LIMIT  # worst case is exactly 12


def test_main_returns_2_when_initial_pending_exceeds_limit(tmp_path, capsys):
    rows = [_high_row(f"{1000 + index}", index) for index in range(1, 38)]
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(_ranking(rows), ensure_ascii=False), encoding="utf-8")
    research_dir = tmp_path / "research"

    assert plan_main(["--ranking", str(ranking_path), "--out-dir", str(research_dir)]) == 2

    assert "dispatch budget exceeded" in capsys.readouterr().err
    manifest = json.loads((research_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dispatch_budget"]["initial_pending"] == 13
    assert len(manifest["batches"]) == 13
    assert all((research_dir / entry["path"]).exists() for entry in manifest["batches"])


def test_budget_counts_only_pending_after_checkpoints(tmp_path):
    research_dir = tmp_path / "research"
    rows = [_high_row(f"{1000 + index}", index) for index in range(1, 38)]
    ranking = _ranking(rows)
    first = write_research_plan(ranking, research_dir)
    assert first["dispatch_budget"]["initial_pending"] == 13
    for entry in first["batches"][:-2]:
        _write_result(research_dir, entry)

    second = write_research_plan(ranking, research_dir)

    assert second["dispatch_budget"]["initial_pending"] == 2
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False), encoding="utf-8")
    assert plan_main(["--ranking", str(ranking_path), "--out-dir", str(research_dir)]) == 0


def test_invalid_checkpoint_is_quarantined_and_requeued(tmp_path):
    research_dir = tmp_path / "research"
    ranking = _ranking([_row("4101", 1), _row("4102", 2)])
    first = write_research_plan(ranking, research_dir)
    entry = first["batches"][0]
    _write_result(research_dir, entry)

    ranking["rows"][0]["pct"] = 9.5  # digest changes; route/risk stay the same
    second = write_research_plan(ranking, research_dir)

    assert second["batches"][0]["status"] == "pending"
    result_path = research_dir / entry["result_path"]
    assert not result_path.exists()
    assert result_path.with_name(result_path.name + ".stale").exists()


def test_orphan_result_is_quarantined(tmp_path):
    research_dir = tmp_path / "research"
    ranking = _ranking([_row("4201", 1)])
    write_research_plan(ranking, research_dir)
    orphan = research_dir / "results" / "batch-099.json"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("{}", encoding="utf-8")

    write_research_plan(ranking, research_dir)

    assert not orphan.exists()
    assert orphan.with_name("batch-099.json.stale").exists()


def test_migration_from_solo_batch_era_requeues_stale_and_reuses_valid(tmp_path):
    """07-15 recovery: replanning over old-era results must quarantine, not stall."""
    research_dir = tmp_path / "research"
    rows = [
        _row("5101", 1, kabutan_news=_tob_news("5101")),
        _row("5102", 2, kabutan_news=_tob_news("5102")),
    ] + [_direct_row(f"{5200 + index}", 2 + index) for index in range(1, 9)]
    ranking = _ranking(rows)
    first = write_research_plan(ranking, research_dir)
    assert first["batches"][0]["codes"] == ["5101", "5102"]  # pooled M&A batch
    stale_entry = first["batches"][0]
    valid_entry = first["batches"][1]
    stale_result = _valid_result(stale_entry)
    stale_result["input_digest"] = "0" * 64  # solo-era digest no longer matches
    _write_result(research_dir, stale_entry, stale_result)
    _write_result(research_dir, valid_entry)

    second = write_research_plan(ranking, research_dir)

    statuses = {entry["batch_id"]: entry["status"] for entry in second["batches"]}
    assert statuses[stale_entry["batch_id"]] == "pending"
    assert statuses[valid_entry["batch_id"]] == "complete"
    assert "invalid" not in statuses.values()
    stale_path = research_dir / stale_entry["result_path"]
    assert not stale_path.exists()
    assert stale_path.with_name(stale_path.name + ".stale").exists()
    assert second["input_digest"] == first["input_digest"]


def test_ledger_is_carried_over_verbatim_on_replan(tmp_path):
    research_dir = tmp_path / "research"
    ranking = _ranking([_row("6101", 1), _row("6102", 2)])
    write_research_plan(ranking, research_dir)
    manifest_path = research_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["ledger"] == {"reservations": {}, "total_reserved": 0}
    spent = {"reservations": {"batch-001": 2, "batch-777": 3}, "total_reserved": 5}
    manifest["ledger"] = spent
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    second = write_research_plan(ranking, research_dir)

    assert second["ledger"] == spent
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["ledger"] == spent


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

    _write_result(research_dir, entry)
    second = write_research_plan(ranking, research_dir)
    assert second["batches"][0]["status"] == "complete"

    ranking["rows"][0]["pct"] = 9.0
    third = write_research_plan(ranking, research_dir)
    # A digest mismatch quarantines the checkpoint and requeues the batch.
    assert third["batches"][0]["status"] == "pending"
    result_path = research_dir / entry["result_path"]
    assert not result_path.exists()
    assert result_path.with_name(result_path.name + ".stale").exists()


def test_plan_rejects_duplicate_codes_and_missing_prev_date():
    with pytest.raises(ValueError, match="duplicate ranking code"):
        build_research_plan(_ranking([_row("5001", 1), _row("5001", 2)]))
    ranking = _ranking([_row("5001", 1)])
    del ranking["prev_date"]
    with pytest.raises(ValueError, match="prev_date"):
        build_research_plan(ranking)
