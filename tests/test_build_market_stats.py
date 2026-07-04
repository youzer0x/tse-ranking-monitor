"""build_market_stats.py の集計ロジックの単体テスト（合成データ・ネット非接触）。

build_records / aggregate_by_sector / detect_divergence_flags / _breadth は
J-Quants から取得した dict を入力に取る純粋関数。ここでは最小の合成 bars/master を
組み立てて、騰落率・流動性フィルタ・セクター加重平均・⚠乖離フラグを検証する。
"""
import pytest

import build_market_stats as bms


def _master_row(code5, mkt="0111", prodcat="011", sector="電気機器"):
    """is_tse_individual を満たす master 行（ProdCat=011・Mkt∈対象・末尾0）。"""
    return {"Code": code5, "ProdCat": prodcat, "Mkt": mkt, "CoName": "会社" + code5,
            "MktNm": "プライム", "ScaleCat": "TOPIX Large70", "S33Nm": sector}


def test_build_records_filters_and_computes():
    master = {
        "10000": _master_row("10000", sector="電気機器"),   # 対象・値付き・大口
        "20000": _master_row("20000", sector="電気機器"),   # 対象・値付き・薄商い
        "30000": _master_row("30000", mkt="0109"),          # 非対象（ETF等の区分）
        "40000": _master_row("40000"),                       # 対象だが前日バー欠損
    }
    today_bars = {
        "10000": {"AdjC": 110.0, "C": 110.0, "Va": 500_000_000},
        "20000": {"AdjC": 90.0, "C": 90.0, "Va": 5_000_000},
        "40000": {"AdjC": 100.0, "C": 100.0, "Va": 200_000_000},
    }
    prev_bars = {
        "10000": {"AdjC": 100.0},
        "20000": {"AdjC": 100.0},
        # 40000 は前日バー無し → priced にカウントされない
    }
    liquid, stats = bms.build_records(today_bars, prev_bars, master, min_turnover=100_000_000)

    assert stats["n_target"] == 3       # 10000/20000/40000（30000 は非対象）
    assert stats["n_priced"] == 2       # 40000 は前日欠損で除外
    assert stats["n_liquid"] == 1       # 20000 は売買代金5M<100M で除外
    assert [r["code5"] for r in liquid] == ["10000"]
    assert liquid[0]["chg_pct"] == pytest.approx(10.0)   # (110-100)/100*100
    assert liquid[0]["sector33"] == "電気機器"


def test_breadth_counts():
    recs = [{"chg_pct": 1.0}, {"chg_pct": -2.0}, {"chg_pct": 0.0}, {"chg_pct": 3.0}]
    assert bms._breadth(recs) == (2, 1, 1)   # up, down, flat


def test_aggregate_by_sector_weighted_and_sorted():
    records = [
        {"sector33": "電気機器", "chg_pct": 10.0, "turnover": 300_000_000},
        {"sector33": "電気機器", "chg_pct": 2.0, "turnover": 100_000_000},
        {"sector33": "銀行業", "chg_pct": -5.0, "turnover": 200_000_000},
    ]
    agg = bms.aggregate_by_sector(records)
    # 売買代金加重騰落率の降順
    assert [a["sector"] for a in agg] == ["電気機器", "銀行業"]
    # 加重平均 = (10*300 + 2*100) / 400 = 8.0
    assert agg[0]["w_mean"] == pytest.approx(8.0)
    assert agg[0]["n"] == 2 and agg[0]["up"] == 2 and agg[0]["down"] == 0
    assert agg[1]["w_mean"] == pytest.approx(-5.0)


def test_detect_divergence_flags_flags_dominant_stock():
    # 大型株1銘柄(+5%)がセクター(他は下落)の加重を押し上げる典型パターン
    records = [
        {"sector33": "電気機器", "chg_pct": 5.0, "turnover": 1_000_000_000,
         "code4": "6501", "name": "大型株"},
        {"sector33": "電気機器", "chg_pct": -1.0, "turnover": 50_000_000,
         "code4": "6502", "name": "小型1"},
        {"sector33": "電気機器", "chg_pct": -1.5, "turnover": 50_000_000,
         "code4": "6503", "name": "小型2"},
        {"sector33": "電気機器", "chg_pct": -1.2, "turnover": 40_000_000,
         "code4": "6504", "name": "小型3"},
        # 均質なセクター（乖離なし → フラグ立たない）
        {"sector33": "銀行業", "chg_pct": -1.0, "turnover": 100_000_000,
         "code4": "8306", "name": "銀行1"},
        {"sector33": "銀行業", "chg_pct": -1.1, "turnover": 100_000_000,
         "code4": "8316", "name": "銀行2"},
    ]
    agg = bms.aggregate_by_sector(records)
    flags = bms.detect_divergence_flags(records, agg)

    assert len(flags) == 1
    f = flags[0]
    assert f["sector"] == "電気機器"
    assert f["dominant"]["code"] == "6501"   # 売買代金最大が支配銘柄
    # 符号乖離・加重と中央値の乖離・支配銘柄の3条件すべてが立つ
    assert set(f["reasons"]) == {"sign_divergence", "weighted_median_spread", "dominant_stock"}


def test_detect_divergence_flags_uniform_sector_no_flag():
    records = [
        {"sector33": "銀行業", "chg_pct": -1.0, "turnover": 100_000_000, "code4": "8306", "name": "a"},
        {"sector33": "銀行業", "chg_pct": -1.1, "turnover": 100_000_000, "code4": "8316", "name": "b"},
        {"sector33": "銀行業", "chg_pct": -0.9, "turnover": 100_000_000, "code4": "8411", "name": "c"},
    ]
    agg = bms.aggregate_by_sector(records)
    assert bms.detect_divergence_flags(records, agg) == []
