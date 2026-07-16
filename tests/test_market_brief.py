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
        "sector_drivers": {"電気機器": [{"code": "1111", "name": "上昇A", "pct": 12.3}]},
        "divergence_flags": [{"sector": "電気機器", "reasons": ["dominant_stock"]}],
    }
    return ranking, evidence, stats


def test_build_market_brief_keeps_completed_claim_source_context_only():
    ranking, evidence, stats = _inputs()
    out = brief.build_market_brief(ranking, evidence, stats)

    assert out["schema_version"] == "market_brief.v2"
    assert "movers" not in out
    assert "sources" not in out
    assert out["clusters"]["theme_clusters"][0].get("raw") is None
    assert out["clusters"]["sector_drivers"] == stats["sector_drivers"]
    assert out["divergence_flags"] == stats["divergence_flags"]
    # status=complete の claim が参照した source だけをコード単位の文脈付きで拾う。
    # unresolved の 2222、未参照の "unused"、source.body は含めない。
    assert out["accepted_evidence"] == [{
        "code": "1111",
        "market_note": "市場向け短文",
        "claims": [{"text": "検証済み", "source_ids": ["1111:s1"]}],
        "sources": [{
            "id": "1111:s1", "label": "会社IR", "url": "https://example.com/a.pdf",
            "source_type": "company_ir", "published_at": "2026-07-15T09:00:00+09:00",
            "window": "material",
        }],
    }]


def test_market_brief_namespaces_item_local_source_ids_by_code():
    ranking, evidence, stats = _inputs()
    evidence["items"][1] = {
        "code": "2222", "status": "complete", "factor": "別の検証済み要因",
        "factor_kind": "報道", "market_note": "別銘柄の市場向け短文",
        "claims": [{"text": "別の検証済み主張", "source_ids": ["s1"]}],
        "sources": [{
            "id": "s1", "label": "報道記事", "url": "https://example.com/b",
            "source_type": "article", "published_at": "2026-07-15T10:00:00+09:00",
            "window": "material",
        }],
    }

    accepted = brief.build_market_brief(ranking, evidence, stats)["accepted_evidence"]

    assert [item["code"] for item in accepted] == ["1111", "2222"]
    assert accepted[0]["claims"][0]["source_ids"] == ["1111:s1"]
    assert accepted[0]["sources"][0]["url"] == "https://example.com/a.pdf"
    assert accepted[1]["claims"][0]["source_ids"] == ["2222:s1"]
    assert accepted[1]["sources"][0]["url"] == "https://example.com/b"


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
