"""build_day_ranking.py の annotate_sector_clusters の単体テスト。

同一 S33（33業種）で複数銘柄が上昇したときにクラスタ注釈を付す決定的関数。
leader は「具体的な TDnet 開示を持つ銘柄を優先、無ければ売買代金最大」。
ネットワーク非接触（rows は rank/disclosures 付与後の想定）。
"""
import build_day_ranking as bdr


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
