"""build_day_ranking.py の annotate_sector_clusters の単体テスト。

同一 S33（33業種）で複数銘柄が上昇したときにクラスタ注釈を付す決定的関数。
leader は「具体的な TDnet 開示を持つ銘柄を優先、無ければ売買代金最大」。
ネットワーク非接触（rows は rank/disclosures 付与後の想定）。
"""
import build_day_ranking as bdr
import datetime
import inspect

import pytest


def _row(code, sec33, sec33_name, pct, turnover_yen, rank, disclosures=None):
    return {
        "code": code, "name": "銘柄" + code, "sec33": sec33, "sec33_name": sec33_name,
        "pct": pct, "rank": rank,
        "turnover_yen": turnover_yen, "turnover_m": round(turnover_yen / 1e6, 1),
        "disclosures": disclosures or [],
    }


def test_single_stock_sector_gets_no_cluster():
    rows = [_row("6501", "16", "電気機器", 6.0, 500_000_000, 1)]
    summary = bdr.annotate_sector_clusters(rows, min_cluster=2)
    assert summary == []
    assert "sector_cluster" not in rows[0]   # 単独銘柄には注釈を付けない


def test_two_stock_sector_forms_cluster_leader_by_turnover():
    # 同業種2銘柄・開示なし → 売買代金最大が leader
    rows = [
        _row("6501", "16", "電気機器", 8.0, 300_000_000, 1),
        _row("6502", "16", "電気機器", 6.0, 900_000_000, 2),   # 売買代金最大
    ]
    summary = bdr.annotate_sector_clusters(rows, min_cluster=2)
    assert len(summary) == 1
    c = summary[0]
    assert c["sec33"] == "16" and c["size"] == 2
    assert c["leader_code"] == "6502"
    assert c["leader_basis"] == "turnover"
    # 各行に peers（自分以外）が付く
    assert rows[0]["sector_cluster"]["peers"][0]["code"] == "6502"


def test_leader_prefers_stock_with_disclosure():
    # 売買代金は 6502 が最大だが、開示を持つ 6501 が leader になる
    rows = [
        _row("6501", "16", "電気機器", 8.0, 300_000_000, 1,
             disclosures=[{"title": "業績上方修正", "time": "14:00"}]),
        _row("6502", "16", "電気機器", 6.0, 900_000_000, 2),
    ]
    summary = bdr.annotate_sector_clusters(rows, min_cluster=2)
    assert summary[0]["leader_code"] == "6501"
    assert summary[0]["leader_basis"] == "disclosure"


def test_min_cluster_boundary():
    # min_cluster=3 のとき、2銘柄のセクターはクラスタにならない
    rows = [
        _row("6501", "16", "電気機器", 8.0, 300_000_000, 1),
        _row("6502", "16", "電気機器", 6.0, 200_000_000, 2),
    ]
    assert bdr.annotate_sector_clusters(rows, min_cluster=3) == []


def test_clusters_sorted_by_size_desc():
    rows = [
        _row("6501", "16", "電気機器", 8.0, 300_000_000, 1),
        _row("6502", "16", "電気機器", 7.0, 200_000_000, 2),
        _row("6503", "16", "電気機器", 6.0, 100_000_000, 3),
        _row("8801", "24", "不動産業", 9.0, 500_000_000, 4),
        _row("8802", "24", "不動産業", 5.0, 400_000_000, 5),
    ]
    summary = bdr.annotate_sector_clusters(rows, min_cluster=2)
    # 電気機器(3) が 不動産業(2) より前
    assert [c["sec33"] for c in summary] == ["16", "24"]
    assert summary[0]["size"] == 3


def _source_rows(session="2026-07-15", prev="2026-07-14", prev5="2026-07-08"):
    master = {
        "11110": {"Code": "11110", "Date": session, "ProdCat": "011", "Mkt": "0111",
                  "CoName": "テスト社", "MktNm": "プライム", "S17": "1", "S17Nm": "食品",
                  "S33": "1", "S33Nm": "水産・農林業", "ScaleCat": "TOPIX Small"},
        "22220": {"Code": "22220", "Date": session, "ProdCat": "011", "Mkt": "0111",
                  "CoName": "別会社", "MktNm": "プライム", "S17": "1", "S17Nm": "食品",
                  "S33": "1", "S33Nm": "水産・農林業", "ScaleCat": "TOPIX Small"},
    }
    now = {
        "11110": {"Code": "11110", "Date": session, "AdjC": 110.0, "C": 110.0, "Va": 20_000_000},
        "22220": {"Code": "22220", "Date": session, "AdjC": 100.0, "C": 100.0, "Va": 20_000_000},
    }
    before = {
        "11110": {"Code": "11110", "Date": prev, "AdjC": 100.0},
        "22220": {"Code": "22220", "Date": prev, "AdjC": 100.0},
    }
    before5 = {
        "11110": {"Code": "11110", "Date": prev5, "AdjC": 90.0},
        "22220": {"Code": "22220", "Date": prev5, "AdjC": 100.0},
    }
    return master, now, before, before5


def test_default_ranking_cap_remains_30():
    assert inspect.signature(bdr.build).parameters["max_rank"].default == 30


def test_source_validation_rejects_incomplete_session_bars_ratio():
    master, now, before, before5 = _source_rows()
    now.pop("22220")

    with pytest.raises(ValueError, match="session/previous bars ratio"):
        bdr.validate_source_data(
            "2026-07-15", "2026-07-14", "2026-07-08", master, now, before, before5)


def test_source_validation_rejects_low_master_coverage():
    master, now, before, before5 = _source_rows()
    master["33330"] = dict(master["22220"], Code="33330", CoName="第三社")

    with pytest.raises(ValueError, match="master/bars coverage"):
        bdr.validate_source_data(
            "2026-07-15", "2026-07-14", "2026-07-08", master, now, before, before5)


def test_source_validation_rejects_wrong_payload_date():
    master, now, before, before5 = _source_rows()
    now["11110"]["Date"] = "2026-07-14"

    with pytest.raises(ValueError, match="requested 2026-07-15"):
        bdr.validate_source_data(
            "2026-07-15", "2026-07-14", "2026-07-08", master, now, before, before5)


def test_build_adds_schema_and_uses_explicit_jst(monkeypatch):
    master, now, before, before5 = _source_rows()
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(bdr.jquants, "master_by_date", lambda _date: master)
    monkeypatch.setattr(
        bdr.jquants, "bars_by_date",
        lambda day: {"2026-07-15": now, "2026-07-14": before, "2026-07-08": before5}[day])
    monkeypatch.setattr(bdr.mcap, "prime_price_cache", lambda *_args: None)
    monkeypatch.setattr(
        bdr.mcap, "compute_one",
        lambda *_args: (200.0, 1_000_000, datetime.date(2026, 3, 31), 1.0, "jquants"))
    monkeypatch.setattr(bdr.tdnet, "disclosures_window", lambda *_args: {})
    monkeypatch.setattr(bdr._impl.time, "sleep", lambda _seconds: None)

    real_datetime = datetime.datetime

    class SpyDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is bdr._impl.JST
            return real_datetime(2026, 7, 15, 17, 0, tzinfo=tz)

    monkeypatch.setattr(bdr._impl.datetime, "datetime", SpyDateTime)
    data = bdr.build(
        "2026-07-15", "2026-07-14", do_kabutan_shares=False, verbose=False)

    assert data["schema_version"] == 1
    assert data["generated_at"] == "2026-07-15 17:00 JST"
    assert data["counts"]["qualifying"] == 1
    assert data["counts"]["ranked"] == 1
    assert data["rows"][0]["rank"] == 1


def test_ranking_document_rejects_count_drift():
    data = {
        "schema_version": 1,
        "criteria": {"max_rank": 30},
        "capped": False,
        "counts": {"qualifying": 1, "ranked": 2, "dropped_turnover": 0, "dropped_mcap": 0},
        "rows": [{"code": "1111", "rank": 1}],
        "dropped_turnover": [],
        "dropped_mcap": [],
    }
    with pytest.raises(ValueError, match="counts.ranked"):
        bdr.validate_ranking_document(data)
