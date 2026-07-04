"""build_market_json.py の単体テスト（標準ライブラリのみ・ネット/APIキー不要）。

中核は validate_market：配信画面(SPA renderMarket)が前提する型に JSON が合致するかを
検証し、崩れていれば SystemExit で止める「番犬」。過去に sector_notes が配列でなく
オブジェクトになり📊市場分析タブが空になった事故があり、その再発を凍結ゴールデンと
改変コピーで検証する。
"""
import copy

import pytest

import build_market_json as bmj


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
    # 実運用の出力（凍結ゴールデン）はそのまま通過する
    bmj.validate_market(market_golden)


def test_validate_market_rejects_object_sector_notes(market_golden):
    # 実際に起きた事故：sector_notes を配列でなくオブジェクトにするとタブが空になる
    broken = copy.deepcopy(market_golden)
    broken["sector_notes"] = {"mark": "▲", "text": "x"}
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)


def test_validate_market_rejects_bad_thesis_type(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["thesis"] = 123   # 文字列でも文字列配列でもない
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)


def test_validate_market_rejects_string_bullets(market_golden):
    # bought.themes[].bullets は文字列配列であるべき（テーマ節を文字列で書くと崩れる）
    broken = copy.deepcopy(market_golden)
    broken["bought"] = {"themes": [{"title": "t", "bullets": "本来は配列"}]}
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)


def test_validate_market_aggregates_multiple_errors(market_golden, capsys):
    # step3.5 は最大2回しか再実行しないため、複数の型崩れは1度に全件報告される
    broken = copy.deepcopy(market_golden)
    broken["sector_notes"] = {"mark": "x", "text": "y"}   # 崩れ1
    broken["disclaimer"] = "本来は配列"                     # 崩れ2
    with pytest.raises(SystemExit):
        bmj.validate_market(broken)
    err = capsys.readouterr().err
    assert "sector_notes" in err and "disclaimer" in err
