"""validate_market_quality.py の単体テスト（標準ライブラリのみ・ネット/APIキー不要）。

品質検査の趣旨は「主張を削らせる」ことではなく「出典を足させる」こと。
2026-07-03 の実事故（news_sources 全空・movers 全リンク空・本文出典ゼロ・
TOPIX 騰落の取り違え）と、07-01/02 のランディングページ出典を再発させないための番犬。
"""
import copy
import json

import pytest

from tse_ranking_monitor.quality import market as vmq


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


# ── 精密主張トリガー ──────────────────────────────────────────
# 判定は文書単位：トリガー語ごとに「リンク付きの言及」が1箇所以上あればよい
# （同一URLの重複掲載を禁止しているため、2回目以降の言及には出典を再掲しない）。
def test_precise_claim_without_any_linked_mention_rejected(market_golden):
    # 「格下げ」は golden に存在しないトリガー＝リンク付き言及ゼロで注入するとエラー
    broken = copy.deepcopy(market_golden)
    broken["overview"]["points"].append("米系証券が投資判断を格下げした。")
    errs = vmq.check_doc(broken)
    assert any("格下げ" in e and "リンク付きの言及が1箇所も無い" in e for e in errs)


def test_precise_claim_with_link_in_same_element_accepted(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append(
        "米系証券が投資判断を格下げした（[トレーダーズウェブ](https://example.com/rating)）。")
    assert vmq.check_doc(doc) == []


def test_trigger_without_same_claim_entity_is_not_document_globally_covered(market_golden):
    # 同じ TOB 語が文書内にあっても、entity を特定しない別 claim まで全体免除しない。
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("電通総研のTOB観測を巡る物色が続いた。")
    assert any("overview.points[5]" in e and "TOB" in e for e in vmq.check_doc(doc))


def test_repeated_claim_with_same_entity_can_reuse_first_source(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("[[電通総研]]のTOB観測を巡る物色が続いた。")
    assert vmq.check_doc(doc) == []


def test_fullwidth_trigger_normalized():
    # ＴＯＢ（全角）も NFKC 正規化で捕捉する（最小構成の合成ドキュメントで検証）
    doc = {
        "thesis": "ＴＯＢ観測で買われた。",
        "overview": {},
        "news_sources": [{"topic": "t", "links": [{"label": "l", "url": "https://example.com/news"}]}],
        "disclaimer": [],
    }
    errs = vmq.check_doc(doc)
    assert any("TOB" in e and "リンク付きの言及が1箇所も無い" in e for e in errs)


def test_theme_matrix_row_link_in_background_suffices(market_golden):
    # theme セルにトリガー語が入っても background 側の文末リンクで満たせる（行単位判定）
    doc = copy.deepcopy(market_golden)
    doc.setdefault("theme_matrix", {}).setdefault("rows", []).append(
        {"side": "buy", "theme": "公開買付観測", "stocks": "テスト銘柄",
         "background": "公開買付の観測が報じられた（[日経](https://www.nikkei.com/article/xxx)）。"})
    assert vmq.check_doc(doc) == []


def test_theme_matrix_row_without_link_rejected(market_golden):
    broken = copy.deepcopy(market_golden)
    broken.setdefault("theme_matrix", {}).setdefault("rows", []).append(
        {"side": "buy", "theme": "公開買付観測", "stocks": "テスト銘柄",
         "background": "公開買付の観測が報じられた。"})
    errs = vmq.check_doc(broken)
    assert any("公開買付" in e and "リンク付きの言及が1箇所も無い" in e for e in errs)


# ── 同一出典URLの重複掲載禁止（URL単位）──────────────────────────
def test_rejects_duplicate_url_in_body(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["overview"]["points"].append("材料視された（[記事](https://example.com/dup)）。")
    broken["overview"]["flow"].append("引き続き材料視（[記事](https://example.com/dup)）。")
    errs = vmq.check_doc(broken)
    assert any("同一URLが本文に2回掲載" in e and "example.com/dup" in e for e in errs)


def test_allows_same_url_in_body_and_news_sources(market_golden):
    # 本文1箇所＋news_sources 1箇所の合計2箇所は許容される正規パターン
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("材料視された（[記事](https://example.com/ok)）。")
    doc["news_sources"].append({"topic": "テスト", "links": [{"label": "記事", "url": "https://example.com/ok"}]})
    assert not any("同一URL" in e for e in vmq.check_doc(doc))


def test_rejects_duplicate_url_in_news_sources(market_golden):
    broken = copy.deepcopy(market_golden)
    url = broken["news_sources"][0]["links"][0]["url"]
    broken["news_sources"].append({"topic": "テスト", "links": [{"label": "重複", "url": url}]})
    errs = vmq.check_doc(broken)
    assert any("news_sources に2回掲載" in e for e in errs)


def test_methodology_universe_disclaimer_excluded(market_golden):
    # フィルタ条件の定型文（時価総額100億円等）は検査対象外
    doc = copy.deepcopy(market_golden)
    doc["methodology"]["lines"].append("**時価総額** ▶ 100億円未満は除外。")
    doc["disclaimer"].append("目標株価等の記載は出典記事の転記であり推奨ではない。")
    assert vmq.check_doc(doc) == []


# ── 拡張トリガー（2026-07-05 監査反映）───────────────────────────
def test_expanded_triggers_without_link_rejected(market_golden):
    # golden にリンク付き言及が無い拡張トリガー語（国内シェア）で検証。
    # 受注残・契約・業務提携・世界首位等は golden 自身がリンク付きで言及済みのため
    # doc-level 判定でカバーされる（それが仕様）。
    broken = copy.deepcopy(market_golden)
    broken["overview"]["points"].append("同製品の国内シェア8割を握る。")
    errs = vmq.check_doc(broken)
    assert any("国内シェア" in e and "リンク付きの言及が1箇所も無い" in e for e in errs)


def test_expanded_trigger_with_link_accepted(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同製品の国内シェア8割を握る（[会社IR](https://example.com/share)）。")
    assert vmq.check_doc(doc) == []


# ── 因果表現の監査（check_warnings・エラーにはしない）────────────
def test_warnings_zero_on_golden(market_golden):
    assert vmq.check_warnings(market_golden) == []


def test_causal_word_without_link_or_hedge_warns(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の発表がセクター全体の物色に点火した。")
    warns = vmq.check_warnings(doc)
    assert any("点火" in w for w in warns)
    assert vmq.check_doc(doc) == []   # WARN はエラーに数えない（exit code に影響しない）


def test_causal_word_with_hedge_no_warn(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の発表が物色に点火したとみられる。")
    assert vmq.check_warnings(doc) == []


def test_causal_word_with_link_no_warn(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の発表がセクター物色に点火した（[記事](https://example.com/ignite)）。")
    assert vmq.check_warnings(doc) == []


def test_causal_word_with_own_data_no_warn(market_golden):
    # 加重・中央値・売買代金など自データの定量文脈が同一要素にあれば検証可能として免除
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("加重+9.86%は同社1銘柄が押し上げた歪み。")
    assert vmq.check_warnings(doc) == []


def test_check_warnings_skips_broken_structure(market_golden, capsys):
    # 構造 NG はエラー側（check_doc）が報告するため warnings は出さない
    broken = copy.deepcopy(market_golden)
    broken["disclaimer"] = "本来は配列"
    assert vmq.check_warnings(broken) == []
    capsys.readouterr()


def test_cli_warn_exit_zero(market_golden, tmp_path, capsys):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の発表がセクター全体の物色に点火した。")
    p = tmp_path / "warn_market.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert vmq.main([str(p)]) == 0
    assert "WARN" in capsys.readouterr().err


# ── 直接材料の帰属監査（TIER_A・2026-07-05 追加。check_warnings・エラーにはしない）──
def test_tier_a_material_without_source_warns(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の決算を受けた見直し買いが継続した。")
    warns = vmq.check_warnings(doc)
    assert any("直接材料" in w and "決算を受け" in w for w in warns)
    assert vmq.check_doc(doc) == []   # WARN は非ブロッキング（exit code に影響しない）


def test_tier_a_zairyoushi_warns(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("月次動向を材料視した買いが先行した。")
    assert any("材料視" in w for w in vmq.check_warnings(doc))


def test_tier_a_with_hedge_no_warn(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("同社の決算を受けた見直し買いが入ったとみられる。")
    assert vmq.check_warnings(doc) == []


def test_tier_a_with_link_no_warn(market_golden):
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("決算を受けた見直し買い（[記事](https://example.com/kessan)）。")
    assert vmq.check_warnings(doc) == []


def test_tier_a_with_own_data_no_warn(market_golden):
    # 加重・売買代金など自データの定量文脈が同一要素にあれば検証可能として免除
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("決算を受け売買代金トップに躍り出た。")
    assert vmq.check_warnings(doc) == []


def test_tier_a_follower_phrasing_not_flagged(market_golden):
    # 「恩恵を受け」はフォロワー（上昇に相乗り＝相関）の正しい語法なので TIER_A の対象にしない
    doc = copy.deepcopy(market_golden)
    doc["overview"]["points"].append("半導体市況回復の恩恵を受け急騰した。")
    assert vmq.check_warnings(doc) == []


# ── エラー集約・構造検証との連携 ───────────────────────────────
def test_errors_aggregated_across_checks(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = []                                   # 崩れ1
    broken["overview"]["points"].append("米系証券が格下げした。")               # 崩れ2（リンク付き言及なし）
    errs = vmq.check_doc(broken)
    assert len(errs) >= 2


def test_structure_failure_short_circuits(market_golden, capsys):
    # 構造が崩れていたら品質検査はせず「構造を先に直せ」の1件のみ返す
    broken = copy.deepcopy(market_golden)
    broken["disclaimer"] = "本来は配列"
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


# ── machine findings / claim-scoped repair ───────────────────
def test_machine_findings_have_stable_shape_and_repair_target(market_golden):
    broken = copy.deepcopy(market_golden)
    broken["news_sources"][0]["links"] = []
    findings = vmq.audit_doc(broken)
    item = next(f for f in findings if f["rule_id"] == "MKT_NEWS_SOURCE_LINK_REQUIRED")
    assert set(item) == {"code", "path", "rule_id", "severity", "message"}
    assert item["severity"] == "ERROR"
    assert vmq.select_repair_targets([item]) == [{
        "code": item["code"], "path": item["path"],
        "rule_ids": ["MKT_NEWS_SOURCE_LINK_REQUIRED"], "severities": ["ERROR"],
        "messages": [item["message"]],
    }]


def test_repair_target_messages_dedupe_truncate_and_cap():
    long_message = "x" * 600
    findings = [
        vmq.finding("p", "R1", "ERROR", long_message),
        vmq.finding("p", "R1", "ERROR", long_message),  # 切り詰め後に重複排除
    ] + [vmq.finding("p", "R%d" % i, "ERROR", "msg-%d" % i) for i in range(2, 9)]
    [target] = vmq.select_repair_targets(findings)
    assert target["messages"][0] == "x" * 500
    assert len(target["messages"]) == 5  # 上限5件
    assert target["messages"][1:] == ["msg-2", "msg-3", "msg-4", "msg-5"]


def test_cli_json_findings_and_repair_targets(market_golden, tmp_path, capsys):
    doc = copy.deepcopy(market_golden)
    doc["news_sources"][0]["links"] = []
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    assert vmq.main([str(path), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "quality_findings.v1"
    assert payload["files"][0]["findings"][0]["rule_id"] == "MKT_NEWS_SOURCE_LINK_REQUIRED"

    assert vmq.main([str(path), "--repair-targets"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"][0]["targets"][0]["path"].startswith("news_sources[0]")

    target_path = tmp_path / "repair" / "market_targets.json"
    assert vmq.main([str(path), "--repair-targets", str(target_path)]) == 1
    assert json.loads(target_path.read_text(encoding="utf-8"))["validator"] == "market"
    assert capsys.readouterr().out == ""
