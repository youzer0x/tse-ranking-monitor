"""validate_market_quality.py の単体テスト（標準ライブラリのみ・ネット/APIキー不要）。

品質検査の趣旨は「主張を削らせる」ことではなく「出典を足させる」こと。
2026-07-03 の実事故（news_sources 全空・movers 全リンク空・本文出典ゼロ・
TOPIX 騰落の取り違え）と、07-01/02 のランディングページ出典を再発させないための番犬。
"""
import copy
import json

import pytest

import validate_market_quality as vmq


# ── golden（修正後 2026-07-03 スナップショット）────────────────
def test_quality_accepts_golden(market_golden):
    assert vmq.check_doc(market_golden) == []


# ── news_sources ─────────────────────────────────────────────
def test_rejects_empty_news_sources_links(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = []
    errs = vmq.check_doc(broken)
    assert any("news_sources[0]" in e and "links が空" in e for e in errs)


def test_rejects_missing_news_sources(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"] = []
    errs = vmq.check_doc(broken)
    assert any("news_sources が空" in e for e in errs)


# ── emph movers ──────────────────────────────────────────────
def test_rejects_emph_mover_without_links(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["movers"]["gainers"].append(
        {"code": "9998", "name": "テスト", "note": "需給で急伸。", "emph": True, "links": []})
    errs = vmq.check_doc(broken)
    assert any("movers.gainers" in e and "emph=true" in e for e in errs)


def test_allows_plain_mover_without_links(market_golden):
    # emph 無し・精密主張無しの mover は links 空でも通る（薄商い小型の需給說明など）
    doc = copy.deepcopy(market_golden)
    doc["movers"]["gainers"].append(
        {"code": "9998", "name": "テスト", "note": "需給で急伸。", "links": []})
    assert vmq.check_doc(doc) == []


# ── 禁止 URL（ランディングページ）──────────────────────────────
@pytest.mark.parametrize("url", [
    "https://minkabu.jp/stock/9956",
    "https://www.nikkei.com/nkd/company/?scode=5801",
    "https://finance.yahoo.co.jp/quote/3436.T",
    "https://kabutan.jp/stock/?code=5942",
    "https://kabutan.jp/stock/finance?code=5942",
    "https://s.kabutan.jp/stocks/4578/news/",
])
def test_rejects_banned_urls(market_golden, url):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = [{"label": "x", "url": url}]
    errs = vmq.check_doc(broken)
    assert any("ランディングページ出典は禁止" in e and url in e for e in errs)


@pytest.mark.parametrize("url", [
    "https://kabutan.jp/news/marketnews/?b=n202607020472",
    "https://s.kabutan.jp/news/n202607010670/",
    "https://minkabu.jp/news/4321000",
    "https://finance.yahoo.co.jp/news/detail/33350b69a9e535e7bd842f4d0b8da2df30dd81af",
])
def test_allows_article_urls(market_golden, url):
    doc = copy.deepcopy(market_golden)
    doc["news_sources"][0]["links"] = [{"label": "記事", "url": url}]
    assert not any("ランディングページ" in e for e in vmq.check_doc(doc))


def test_banned_url_in_body_markdown_rejected(market_golden):
    # 本文中の Markdown リンクも禁止 URL 検査の対象
    broken = copy.deepcopy(market_golden)
    broken["overview"]["points"].append("急落した（[みんかぶ](https://minkabu.jp/stock/5706)）。")
    errs = vmq.check_doc(broken)
    assert any("overview.points" in e and "ランディングページ" in e for e in errs)


def test_banned_url_in_mover_links_rejected(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["movers"]["losers"].append(
        {"code": "9997", "name": "テスト", "note": "反落。",
         "links": [{"label": "みんかぶ", "url": "https://minkabu.jp/stock/9997"}]})
    errs = vmq.check_doc(broken)
    assert any("movers.losers" in e and "ランディングページ" in e for e in errs)


# ── 精密主張トリガー ──────────────────────────────────────────
def test_precise_claim_without_link_rejected(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["overview"]["points"].append("野村證券が目標株価を大幅に引き上げた。")
    errs = vmq.check_doc(broken)
    assert any("overview.points" in e and "目標株価" in e and "出典リンクが無い" in e for e in errs)


def test_precise_claim_with_link_in_same_element_accepted(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append(
        "野村證券が目標株価を大幅に引き上げた（[トレーダーズウェブ](https://example.com/rating)）。")
    assert vmq.check_doc(doc) == []


def test_fullwidth_trigger_normalized(market_golden):
    # ＴＯＢ（全角）も NFKC 正規化で捕捉する
    broken = copy.deepcopy(market_golden)
    broken["overview"]["flow"].append("ＴＯＢ観測で急伸した。")
    errs = vmq.check_doc(broken)
    assert any("overview.flow" in e and "出典リンクが無い" in e for e in errs)


def test_mover_note_precise_claim_relaxed_by_entry_links(market_golden):
    # movers は材料列にリンクが併記描画されるため、行の links 非空なら note 内リンク不要
    doc = copy.deepcopy(market_golden)
    doc["movers"]["gainers"].append(
        {"code": "9996", "name": "テスト", "note": "上方修正を発表し急伸。",
         "links": [{"label": "会社IR", "url": "https://example.com/ir.pdf"}]})
    assert vmq.check_doc(doc) == []


def test_mover_note_precise_claim_without_any_link_rejected(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["movers"]["gainers"].append(
        {"code": "9996", "name": "テスト", "note": "上方修正を発表し急伸。", "links": []})
    errs = vmq.check_doc(broken)
    assert any("movers.gainers" in e and "上方修正" in e for e in errs)


def test_theme_matrix_row_link_in_background_suffices(market_golden):
    # theme セルにトリガー語が入っても background 側の文末リンクで満たせる（行単位判定）
    doc = copy.deepcopy(market_golden)
    doc.setdefault("theme_matrix", {}).setdefault("rows", []).append(
        {"side": "buy", "theme": "電通総研TOB", "stocks": "電通総研",
         "background": "富士通による完全子会社化が報じられた（[日経](https://www.nikkei.com/article/xxx)）。"})
    assert vmq.check_doc(doc) == []


def test_theme_matrix_row_without_link_rejected(market_golden):
    broken = copy.deepcopy(market_golden)
    broken.setdefault("theme_matrix", {}).setdefault("rows", []).append(
        {"side": "buy", "theme": "電通総研TOB", "stocks": "電通総研",
         "background": "富士通による完全子会社化が報じられた。"})
    errs = vmq.check_doc(broken)
    assert any("theme_matrix.rows" in e and "TOB" in e for e in errs)


def test_methodology_universe_disclaimer_excluded(market_golden):
    # フィルタ条件の定型文（時価総額100億円等）は検査対象外
    doc = copy.deepcopy(market_golden)
    doc["methodology"]["lines"].append("**時価総額** ▶ 100億円未満は除外。")
    doc["disclaimer"].append("目標株価等の記載は出典記事の転記であり推奨ではない。")
    assert vmq.check_doc(doc) == []


# ── エラー集約・構造検証との連携 ───────────────────────────────
def test_errors_aggregated_across_checks(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = []                                   # 崩れ1
    broken["overview"]["points"].append("目標株価を引き上げた。")               # 崩れ2
    errs = vmq.check_doc(broken)
    assert len(errs) >= 2


def test_structure_failure_short_circuits(market_golden, capsys):
    # 構造が崩れていたら品質検査はせず「構造を先に直せ」の1件のみ返す
    broken = copy.deepcopy(market_golden)
    broken["sector_notes"] = {"mark": "x", "text": "y"}
    errs = vmq.check_doc(broken)
    capsys.readouterr()  # validate_market の die 出力を回収
    assert len(errs) == 1 and "構造検証" in errs[0]


# ── CLI ──────────────────────────────────────────────────────
def test_cli_exit_codes(market_golden, tmp_path, capsys):
    good = tmp_path / "good_market.json"
    good.write_text(json.dumps(market_golden, ensure_ascii=False), encoding="utf-8")
    assert vmq.main([str(good)]) == 0

    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = []
    bad = tmp_path / "bad_market.json"
    bad.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    assert vmq.main([str(bad)]) == 1
    assert vmq.main([str(good), str(bad)]) == 1   # 1本でも NG なら非ゼロ

    assert vmq.main([str(tmp_path / "missing.json")]) == 1
    capsys.readouterr()
