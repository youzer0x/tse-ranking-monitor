"""build_market_json.py の単体テスト（標準ライブラリのみ・ネット/APIキー不要）。

中核は validate_market：配信画面(SPA renderMarket)が前提する型に JSON が合致するかを
検証し、崩れていれば SystemExit で止める「番犬」。過去にフラグメントの型崩れで
📊市場分析タブが空になった事故があり、その再発を凍結ゴールデンと改変コピーで検証する。
2026-07 改修で strip / sector_notes / bought / sold は廃止（余剰キーとして無視され、
旧データのゴールデンは引き続き受理される）。sectors33 へのセクター代表銘柄の付与は
join_drivers が担う。
"""
import copy

import pytest

from tse_ranking_monitor.market import assemble as bmj


# ── parse_num ────────────────────────────────────────────────
@pytest.mark.parametrize("raw, expected", [
    ("+15.09", 15.09),
    ("-0.64", -0.64),
    ('"1,535.3"', 1535.3),   # ダブルクオート＋カンマ
    ("58,619.1", 58619.1),
    ("", None),
    ("-", None),
    ("—", None),             # 全角ダッシュ
    (None, None),
])
def test_parse_num(raw, expected):
    assert bmj.parse_num(raw) == expected


def test_parse_num_unparseable_dies():
    with pytest.raises(SystemExit):
        bmj.parse_num("abc")


# ── _oku_jp（億円 → 表示文字列）───────────────────────────────
@pytest.mark.parametrize("oku, expected", [
    (None, "-"),
    (500, "500億円"),
    (9999, "9,999億円"),
    (10000, "約1.0兆円"),   # 1兆以上は兆表記
    (12345, "約1.2兆円"),
])
def test_oku_jp(oku, expected):
    assert bmj._oku_jp(oku) == expected


# ── top_stock_out（出力スキーマの固定）─────────────────────────
def test_top_stock_out_none():
    assert bmj.top_stock_out(None) is None


def test_top_stock_out_keeps_only_schema_keys():
    src = {"code": "7203", "name": "トヨタ", "turnover_oku": 123.4, "pct": 1.5}
    assert bmj.top_stock_out(src) == {"code": "7203", "name": "トヨタ", "turnover_oku": 123.4}


# ── fill_snapshot_row（auto 行を stats から機械生成）──────────────
def test_fill_snapshot_row_passthrough_without_auto():
    row = {"label": "任意", "value": "手書き"}
    assert bmj.fill_snapshot_row(row, {}) == row


def test_fill_snapshot_row_topix_generated():
    row = {"auto": "topix"}
    stats = {"topix_pct": 1.23, "topix_close": 2850}
    out = bmj.fill_snapshot_row(row, stats)
    assert out["label"] == "TOPIX（終値ベース）"
    assert out["value"].startswith("+1.23%")
    assert "auto" not in out          # auto キーは出力に残さない


def test_fill_snapshot_row_explicit_value_wins():
    row = {"auto": "topix", "value": "取得不可"}
    out = bmj.fill_snapshot_row(row, {"topix_pct": 1.23})
    assert out["value"] == "取得不可"   # 明示値を優先（auto は欠けた欄のみ補完）


def test_fill_snapshot_row_bad_kind_dies():
    with pytest.raises(SystemExit):
        bmj.fill_snapshot_row({"auto": "unknown"}, {})


# ── check_url ────────────────────────────────────────────────
def test_check_url_accepts_http_and_https():
    bmj.check_url("https://example.com/a", "ctx")  # 例外を投げなければ合格
    bmj.check_url("http://example.com/b", "ctx")


@pytest.mark.parametrize("bad", ["ftp://x", "javascript:alert(1)", "example.com", None])
def test_check_url_rejects_non_http(bad):
    with pytest.raises(SystemExit):
        bmj.check_url(bad, "ctx")


# ── validate_market（本丸）──────────────────────────────────────
def test_validate_market_accepts_golden(market_golden):
    # 実運用の出力（凍結ゴールデン）はそのまま通過する。旧スキーマの余剰キー
    # （strip / sector_notes / bought / sold。2026-07 改修で廃止）は無視される。
    bmj.validate_market(market_golden)


def test_validate_market_rejects_bad_thesis_type(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["thesis"] = 123   # 文字列でも文字列配列でもない
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)


def test_validate_market_rejects_legacy_theme_matrix(market_golden, capsys):
    # 旧 {theme,bought,sold} 形式は廃止（07-01/02 が旧形式のまま公開されていた監査指摘の再発防止）。
    # 従来も stocks/background 欠落で落ちていたが、原因が分かる誘導メッセージを出す。
    broken = copy.deepcopy(market_golden)
    broken["theme_matrix"] = {"rows": [{"theme": "半導体", "bought": "A社", "sold": "B社"}]}
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)
    err = capsys.readouterr().err
    assert "theme_matrix" in err and "旧" in err


def test_validate_market_aggregates_multiple_errors(market_golden, capsys):
    # step3.5 は最大2回しか再実行しないため、複数の型崩れは1度に全件報告される
    broken = copy.deepcopy(market_golden)
    broken["thesis"] = 123                # 崩れ1（文字列でも文字列配列でもない）
    broken["disclaimer"] = "本来は配列"     # 崩れ2
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)
    err = capsys.readouterr().err
    assert "thesis" in err and "disclaimer" in err


# ── join_drivers（stats の代表銘柄を sectors33 へ付与）─────────────────
def _sectors_rows():
    """read_sector_csv の返り値を模した最小の sectors33 行。"""
    return [{"name": "電気機器", "w_pct": 6.55}, {"name": "銀行業", "w_pct": -1.2}]


def test_join_drivers_attaches_drivers_in_contribution_order():
    sectors = _sectors_rows()
    stats = {"sector_drivers": {
        "電気機器": [{"code": "6501", "name": "日立", "pct": 5.0, "share_pct": 40.0},
                     {"code": "6702", "name": "富士通", "pct": 4.0, "share_pct": 30.0}]}}
    bmj.join_drivers(sectors, stats)
    # 各 driver は {code,name,pct} に固定（share_pct は SPA が使わないため落とす）。順序維持。
    assert sectors[0]["drivers"] == [{"code": "6501", "name": "日立", "pct": 5.0},
                                     {"code": "6702", "name": "富士通", "pct": 4.0}]
    assert "drivers" not in sectors[1]   # sector_drivers に無いセクターへは付けない


def test_join_drivers_unknown_sector_dies():
    stats = {"sector_drivers": {"未知業種": [{"code": "0000", "name": "x", "pct": 0.0}]}}
    with pytest.raises(SystemExit):
        bmj.join_drivers(_sectors_rows(), stats)


@pytest.mark.parametrize("stats", [None, {}, {"sector_drivers": None}])
def test_join_drivers_noop_without_stats(stats):
    # --stats 無し・旧 stats（sector_drivers 無し）では何も付けない（SPA が「—」表示）
    sectors = _sectors_rows()
    bmj.join_drivers(sectors, stats)
    assert all("drivers" not in s for s in sectors)
