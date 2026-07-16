"""build_market_stats.py の集計ロジックの単体テスト（合成データ・ネット非接触）。

build_records / aggregate_by_sector / detect_divergence_flags / sector_drivers /
_breadth は J-Quants から取得した dict を入力に取る純粋関数。ここでは最小の合成
bars/master を組み立てて、騰落率・流動性フィルタ・セクター加重平均・⚠乖離フラグ・
セクター代表銘柄（sector_drivers）を検証する。
"""
import pytest

from tse_ranking_monitor.market import stats as bms


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


# ── sector_drivers（セクター騰落を主導した代表銘柄の選定・寄与順1〜2件の配列）──
def test_sector_drivers_up_sector_picks_max_positive_contribution():
    # 上昇セクター（加重>=0）では寄与 chg×va が最大の銘柄が代表になる。
    # 第2位（寄与2億 < 首位3,000億の50%）は併記されない。
    records = [
        {"sector33": "電気機器", "chg_pct": 10.0, "turnover": 300_000_000,
         "code4": "6501", "name": "押し上げ役"},
        {"sector33": "電気機器", "chg_pct": 2.0, "turnover": 100_000_000,
         "code4": "6502", "name": "脇役"},
    ]
    drv = bms.sector_drivers(records)
    ds = drv["電気機器"]
    assert len(ds) == 1
    assert ds[0]["code"] == "6501"
    assert ds[0]["pct"] == 10.0
    assert ds[0]["share_pct"] == 75.0   # 3億 / 4億 = 75%


def test_sector_drivers_down_sector_picks_most_negative_contribution():
    # 大型株1銘柄(-5%)が加重を負に沈めるケース：他が＋でも押し下げ役が代表になる
    records = [
        {"sector33": "銀行業", "chg_pct": -5.0, "turnover": 1_000_000_000,
         "code4": "8306", "name": "大型株"},
        {"sector33": "銀行業", "chg_pct": 2.0, "turnover": 100_000_000,
         "code4": "8316", "name": "小型1"},
        {"sector33": "銀行業", "chg_pct": 3.0, "turnover": 50_000_000,
         "code4": "8411", "name": "小型2"},
    ]
    # 加重 = (-5*1000 + 2*100 + 3*50) / 1150 pt < 0 → 寄与が最も負の 8306 のみが代表
    # （小型1・小型2 は上昇＝方向が逆なので併記対象外）
    drv = bms.sector_drivers(records)
    ds = drv["銀行業"]
    assert len(ds) == 1
    assert ds[0]["code"] == "8306"
    assert ds[0]["pct"] == -5.0
    assert ds[0]["share_pct"] == 87.0   # 10億 / 11.5億 = 86.95… → 87.0


def test_sector_drivers_co_leaders_are_listed_in_contribution_order():
    # 第2位の同方向寄与が首位の50%以上なら共同主導として併記（最大2銘柄・寄与順）
    records = [
        {"sector33": "機械", "chg_pct": 10.0, "turnover": 300_000_000,
         "code4": "6227", "name": "首位"},
        {"sector33": "機械", "chg_pct": 8.0, "turnover": 200_000_000,
         "code4": "6327", "name": "共同主導"},   # 寄与16億 ≥ 首位30億×0.5
        {"sector33": "機械", "chg_pct": 1.0, "turnover": 100_000_000,
         "code4": "6103", "name": "脇役"},       # 寄与1億 → 3位以下は常に対象外
    ]
    drv = bms.sector_drivers(records)
    assert [d["code"] for d in drv["機械"]] == ["6227", "6327"]


def test_sector_drivers_opposite_direction_stock_never_co_listed():
    # セクターが上昇方向のとき、寄与の絶対値が大きくても下落銘柄は併記しない
    records = [
        {"sector33": "化学", "chg_pct": 10.0, "turnover": 300_000_000,
         "code4": "4001", "name": "押し上げ役"},
        {"sector33": "化学", "chg_pct": -20.0, "turnover": 140_000_000,
         "code4": "4002", "name": "逆行安"},    # 寄与-28億（逆方向）
    ]
    # 加重 = (30億-28億)/4.4億 > 0 → 上昇方向。逆行安は対象外で1銘柄のみ
    drv = bms.sector_drivers(records)
    assert [d["code"] for d in drv["化学"]] == ["4001"]


def test_sector_drivers_single_stock_sector():
    records = [
        {"sector33": "空運業", "chg_pct": 1.234, "turnover": 200_000_000,
         "code4": "9201", "name": "単独銘柄"},
    ]
    drv = bms.sector_drivers(records)
    assert drv["空運業"] == [{"code": "9201", "name": "単独銘柄",
                              "pct": 1.23, "share_pct": 100.0}]


def test_sector_drivers_name_nfkc_normalized():
    # 全角英数の銘柄名は NFKC 正規化で半角化される（乖離フラグと同じ規約）
    records = [
        {"sector33": "サービス業", "chg_pct": 5.0, "turnover": 100_000_000,
         "code4": "9999", "name": "ＡＢＣホールディングス"},
    ]
    drv = bms.sector_drivers(records)
    assert drv["サービス業"][0]["name"] == "ABCホールディングス"


def test_build_stats_json_has_sector_drivers_and_no_strip_default():
    # strip 廃止に伴い strip_default は出力されず、sector_drivers がそのまま載る
    liquid = [{"sector33": "電気機器", "chg_pct": 10.0, "turnover": 300_000_000,
               "code4": "6501", "code5": "65010", "name": "押し上げ役"}]
    agg33 = bms.aggregate_by_sector(liquid)
    drivers = bms.sector_drivers(liquid)
    universe = {"n_target": 1, "n_priced": 1, "n_liquid": 1,
                "min_turnover_yen": 100_000_000}
    stats = bms.build_stats_json(
        "2026-07-03", "2026-07-02", "2026-07-03 18:00 JST", 1.24, 4064.6, 4014.9,
        universe, liquid, agg33, drivers, [])
    assert "strip_default" not in stats
    assert stats["sector_drivers"] == {
        "電気機器": [{"code": "6501", "name": "押し上げ役", "pct": 10.0, "share_pct": 100.0}]}


def test_resolve_out_dir_defaults_to_session_worktree_and_honors_override():
    assert bms.resolve_out_dir("2026-07-15") == \
        bms.os.path.join(".work", "2026-07-15", "market")
    assert bms.resolve_out_dir("2026-07-15", "custom/output") == "custom/output"
