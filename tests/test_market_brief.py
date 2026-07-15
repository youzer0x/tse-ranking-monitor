import json

from tse_ranking_monitor.market import brief


def _inputs():
    ranking = {
        "session_date": "2026-07-15",
        "rows": [
            {"rank": 1, "code": "1111", "name": "上昇A", "pct": 12.3,
             "turnover_m": 900.0, "factor": "公開側の旧文", "factor_kind": "テーマ"},
            {"rank": 2, "code": "2222", "name": "上昇B", "pct": 9.1,
             "turnover_m": 500.0, "factor": "未解決", "factor_kind": "テーマ"},
        ],
        "theme_clusters": [{"sec33": "3650", "name": "電気機器", "size": 2,
                            "members": ["1111", "2222"], "leader_code": "1111",
                            "leader_basis": "disclosure", "raw": "drop"}],
    }
    evidence = {
        "schema_version": "evidence.v1",
        "session_date": "2026-07-15",
        "items": [
            {"code": "1111", "status": "complete", "factor": "検証済み要因",
             "factor_kind": "開示", "market_note": "市場向け短文", "claims": [
                 {"text": "検証済み", "source_ids": ["s1"]}],
             "sources": [
                 {"id": "s1", "label": "会社IR", "url": "https://example.com/a.pdf",
                  "source_type": "company_ir", "published_at": "2026-07-15T09:00:00+09:00",
                  "window": "material", "body": "large raw body must not leak"},
                 {"id": "unused", "label": "未使用", "url": "https://example.com/unused"},
             ]},
            {"code": "2222", "status": "unresolved", "factor": "未解決",
             "factor_kind": "テーマ", "market_note": "再利用不可", "sources": []},
        ],
    }
    stats = {
        "session_date": "2026-07-15", "prev_date": "2026-07-14",
        "generated_at": "2026-07-15 17:00 JST", "topix_pct": 0.4,
        "breadth": {"up": 1000, "down": 500, "flat": 20},
        "universe": {"n_liquid": 1520},
        "selected_gainers": [{"code": "1111"}, {"code": "2222"}],
        "selected_losers": [{"code": "9999", "name": "下落C", "pct": -8.0,
                              "sector33": "サービス業"}],
        "movers_context": {"9999": [
            {"date": "2026-07-15", "time": "10:00", "title": "業績予想修正",
             "url": "raw URL is deliberately omitted"}]},
        "sector_drivers": {"電気機器": [{"code": "1111", "name": "上昇A", "pct": 12.3}]},
        "divergence_flags": [{"sector": "電気機器", "reasons": ["dominant_stock"]}],
    }
    return ranking, evidence, stats


def test_build_market_brief_reuses_only_completed_stage2_evidence():
    ranking, evidence, stats = _inputs()
    out = brief.build_market_brief(ranking, evidence, stats)

    assert out["schema_version"] == "market_brief.v1"
    assert [item["code"] for item in out["movers"]["gainers"]] == ["1111"]
    gainer = out["movers"]["gainers"][0]
    assert gainer["factor"] == "検証済み要因"
    assert gainer["factor_kind"] == "開示"
    assert gainer["market_note"] == "市場向け短文"
    assert gainer["source_ids"] == ["s1"]

    assert out["movers"]["losers"] == [{
        "code": "9999", "name": "下落C", "pct": -8.0, "sector33": "サービス業",
        "context": [{"date": "2026-07-15", "time": "10:00", "title": "業績予想修正"}],
    }]
    assert out["clusters"]["theme_clusters"][0].get("raw") is None
    assert out["divergence_flags"] == stats["divergence_flags"]
    assert out["sources"] == [{
        "id": "s1", "label": "会社IR", "url": "https://example.com/a.pdf",
        "source_type": "company_ir", "published_at": "2026-07-15T09:00:00+09:00",
        "window": "material",
    }]


def test_market_brief_falls_back_to_ranking_gainers_when_stats_has_no_selection():
    ranking, evidence, stats = _inputs()
    stats.pop("selected_gainers")
    assert [item["code"] for item in brief.build_market_brief(ranking, evidence, stats)["movers"]["gainers"]] == ["1111"]


def test_market_brief_rejects_session_mismatch():
    ranking, evidence, stats = _inputs()
    evidence["session_date"] = "2026-07-14"
    try:
        brief.build_market_brief(ranking, evidence, stats)
    except ValueError as exc:
        assert "session_date" in str(exc)
    else:
        raise AssertionError("session mismatch must fail")


def test_market_brief_cli_writes_private_artifact(tmp_path, capsys):
    ranking, evidence, stats = _inputs()
    paths = {}
    for name, data in (("ranking", ranking), ("evidence", evidence), ("stats", stats)):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    out = tmp_path / "market" / "brief.json"
    assert brief.main([
        "--ranking", str(paths["ranking"]), "--evidence", str(paths["evidence"]),
        "--stats", str(paths["stats"]), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["kind"] == "market_brief"
    assert "OK" in capsys.readouterr().err
