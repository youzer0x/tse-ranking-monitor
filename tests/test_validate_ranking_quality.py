"""validate_ranking_quality.py の単体テスト（標準ライブラリ＋vmq のみ・ネット/APIキー不要）。

市場分析タブと同一基準を変動要因（factor）に適用する番犬。監査（audits/2026-07-05-factor-quality.md）
が検出した「開示タグなのに窓内開示なし」「材料日付のドリフト」「無出典の因果断定」を再発させない。
趣旨は主張を削らせることではなく、再タグ・出典追加・推定表現化で正確性を担保させること。
"""
import copy
import json

import pytest

from tse_ranking_monitor.quality import ranking as vrq


# ── golden（全チェックを通す合成サンプル）────────────────────────
def test_quality_accepts_golden(ranking_golden):
    assert vrq.check_ranking(ranking_golden) == []


def test_warnings_zero_on_golden(ranking_golden):
    assert vrq.check_ranking_warnings(ranking_golden) == []


# ── check1 [ERROR]：開示タグは窓内 TDnet 開示が必須 ──────────────
def test_kaiji_without_disclosure_rejected(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["disclosures"] = []
    errs = vrq.check_ranking(broken)
    assert any("factor_kind=開示" in e and "disclosures[] が空" in e for e in errs)


def test_bracketed_kind_normalized_still_checked(ranking_golden):
    # factor_kind="[開示]"（角括弧付き）も NFKC/strip で開示扱い → disclosures 空なら C1
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["factor_kind"] = "[開示]"
    broken["rows"][0]["disclosures"] = []
    assert any("disclosures[] が空" in e for e in vrq.check_ranking(broken))


def test_houdou_without_disclosure_not_error(ranking_golden):
    # 報道は TDnet に出ない材料（レーティング等）＝ disclosures 空でも C1 の対象外
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][2]["disclosures"] = []
    assert not any("disclosures[] が空" in e for e in vrq.check_ranking(doc))


def test_thema_without_disclosure_not_error(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["disclosures"] = []
    assert vrq.check_ranking(doc) == []


# ── check6 [ERROR]：禁止ランディングページURL ─────────────────────
@pytest.mark.parametrize("url", [
    "https://minkabu.jp/stock/9956",
    "https://www.nikkei.com/nkd/company/?scode=5801",
    "https://finance.yahoo.co.jp/quote/3436.T",
    "https://kabutan.jp/stock/?code=5942",
    "https://kabutan.jp/stock/finance?code=5942",
    "https://s.kabutan.jp/stocks/4578/news/",
])
def test_banned_pdf_url_in_disclosure_rejected(ranking_golden, url):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["disclosures"][0]["pdf_url"] = url
    errs = vrq.check_ranking(broken)
    assert any("ランディングページ出典は禁止" in e and url in e for e in errs)


def test_banned_url_in_factor_markdown_rejected(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = "急伸（[みんかぶ](https://minkabu.jp/stock/5706)）。"
    errs = vrq.check_ranking(broken)
    assert any(".factor" in e and "ランディングページ出典は禁止" in e for e in errs)


def test_release_tdnet_pdf_allowed(ranking_golden):
    # golden の release.tdnet.info PDF は禁止対象ではない
    assert not any("ランディングページ" in e for e in vrq.check_ranking(ranking_golden))


def test_kabutan_news_landing_url_not_banned(ranking_golden):
    # kabutan_news[].url（上流収集の source hint）は検査対象外＝ kabutan 銘柄ページでも C6 は出さない
    assert not any("ランディングページ" in e for e in vrq.check_ranking(ranking_golden))


# ── check2 [WARN]：開示の材料日付ドリフト ─────────────────────────
def test_kaiji_date_drift_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["disclosures"][0]["date"] = "2026-07-01"
    broken["rows"][0]["factor"] = "6/30に複数適時開示。M&A実現を材料に大幅高。"
    warns = vrq.check_ranking_warnings(broken)
    assert any("材料日付のドリフト" in w and "6/30" in w for w in warns)


def test_kaiji_date_match_no_warn(ranking_golden):
    # golden 行0 は factor に 2026-07-02 を挙げ disclosure.date も 2026-07-02 ＝ 一致で無警告
    assert not any("材料日付のドリフト" in w for w in vrq.check_ranking_warnings(ranking_golden))


def test_kaiji_multiple_dates_one_matches_no_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][0]["factor"] = "6/28の関連報道以来、2026-07-02に開示を好感。"
    assert not any("材料日付のドリフト" in w for w in vrq.check_ranking_warnings(doc))


def test_kaiji_no_date_token_no_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][0]["factor"] = "前日引け後の開示を好感し大幅高。"
    assert not any("材料日付のドリフト" in w for w in vrq.check_ranking_warnings(doc))


def test_thema_date_drift_not_warned(ranking_golden):
    # C2 は開示タグ限定。テーマ行が過去日付を書いてもドリフト警告は出さない
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "6/9の本決算発表以来の継続物色。当日固有材料なし。"
    assert not any("材料日付のドリフト" in w for w in vrq.check_ranking_warnings(doc))


def test_fullwidth_date_normalized(ranking_golden):
    # 全角 ６/３０ も NFKC 正規化で disclosure.date 2026-06-30 と一致＝無警告
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][0]["disclosures"][0]["date"] = "2026-06-30"
    doc["rows"][0]["factor"] = "６/３０に開示を好感し大幅高。"
    assert not any("材料日付のドリフト" in w for w in vrq.check_ranking_warnings(doc))


# ── check3 [WARN]：無出典の因果語・直接材料帰属 ─────────────────
def test_causal_word_unsourced_thema_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = "同社の材料がセクター物色に点火した。"
    warns = vrq.check_ranking_warnings(broken)
    assert any("因果表現" in w and "点火" in w for w in warns)
    assert vrq.check_ranking(broken) == []   # WARN は非ブロッキング（exit code に影響しない）


def test_causal_word_with_hedge_no_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "同社の材料が物色に点火したとみられる。"
    assert not any("因果表現" in w for w in vrq.check_ranking_warnings(doc))


def test_causal_word_in_kaiji_with_disclosure_no_warn(ranking_golden):
    # 開示タグ＋窓内 disclosure は一次開示が [開示PDF] として自動リンクされるため因果語を免除
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][0]["factor"] = "開示を好感し、セクター物色を主導した。"
    assert not any("因果表現" in w for w in vrq.check_ranking_warnings(doc))


def test_causal_word_with_inline_link_no_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "物色が波及した（[記事](https://example.com/x)）。"
    assert not any("因果表現" in w for w in vrq.check_ranking_warnings(doc))


def test_causal_word_with_own_data_no_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "売買代金トップで相場を押し上げた。"
    assert not any("因果表現" in w for w in vrq.check_ranking_warnings(doc))


def test_tier_a_material_unsourced_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = "決算を受けた見直し買いが継続した。"
    warns = vrq.check_ranking_warnings(broken)
    assert any("直接材料" in w and "決算を受け" in w for w in warns)


def test_tier_a_follower_phrasing_not_flagged(ranking_golden):
    # 「恩恵を受け」はフォロワー（相関）の正しい語法なので TIER_A の対象にしない
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "半導体市況回復の恩恵を受け急騰した。"
    assert not any("直接材料" in w for w in vrq.check_ranking_warnings(doc))


def test_report_attribution_unsourced_warns(ranking_golden):
    # 「〜との報道」等の外部報道の断定に出典も推定マーカーも無い＝岡野バルブ型（監査 §2 の再発防止）
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = "次世代型原子炉建設の具体的検討が浮上との報道で急騰。"
    warns = vrq.check_ranking_warnings(broken)
    assert any("報道帰属" in w for w in warns)
    assert vrq.check_ranking(broken) == []   # WARN は非ブロッキング


def test_report_attribution_with_hedge_no_warn(ranking_golden):
    # 「〜報道を受けた…連れ高」のように推定表現で書けば警告しない（TVE/中北の連れ高型）
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "原子炉建設検討報道を受けた原子力テーマで、同業急騰の連れ高。"
    assert not any("報道帰属" in w for w in vrq.check_ranking_warnings(doc))


# ── check4 [WARN]：報道タグの企業アクション語が無裏付け ───────────
def test_corp_action_houdou_no_backing_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][2]["kabutan_news"] = []
    broken["rows"][2]["factor"] = "米系証券が投資判断を格下げした。"
    warns = vrq.check_ranking_warnings(broken)
    assert any("精密イベント" in w and "格下げ" in w for w in warns)


def test_corp_action_with_kabutan_news_no_warn(ranking_golden):
    # golden 行2 は 格上げ・目標株価 を kabutan_news で裏付け済み＝無警告
    assert not any("精密イベント" in w for w in vrq.check_ranking_warnings(ranking_golden))


def test_corp_action_theme_not_check4(ranking_golden):
    # テーマ行が同業他社の企業アクションを背景として引くのは正当＝C4 の対象外
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "同業のアナリスト格上げに連想した買い。"
    assert not any("精密イベント" in w for w in vrq.check_ranking_warnings(doc))


def test_descriptive_keiyaku_not_check4(ranking_golden):
    # 「契約」は RANKING_CLAIM_TRIGGERS から除外＝報道行で無裏付けでも C4 は出さない
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][2]["kabutan_news"] = []
    doc["rows"][2]["factor"] = "大型の供給契約を公表し買われた。"
    assert not any("精密イベント" in w for w in vrq.check_ranking_warnings(doc))


def test_fullwidth_tob_houdou_check4(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][2]["kabutan_news"] = []
    broken["rows"][2]["factor"] = "ＴＯＢ観測が広がった。"     # 全角 → NFKC で TOB
    assert any("精密イベント" in w and "TOB" in w for w in vrq.check_ranking_warnings(broken))


# ── check5 [WARN]：factor_kind / factor の hygiene ────────────────
def test_empty_factor_kind_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["factor_kind"] = ""
    assert any("factor_kind が空" in w for w in vrq.check_ranking_warnings(broken))


def test_invalid_factor_kind_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["factor_kind"] = "確認不可"
    assert any("既定外" in w for w in vrq.check_ranking_warnings(broken))


def test_empty_factor_warns(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = ""
    assert any("factor が空" in w for w in vrq.check_ranking_warnings(broken))


def test_material_unconfirmed_fallback_no_warn(ranking_golden):
    # 「材料未確認」等は非空テキスト＝ factor 空警告の対象にならない（認可フォールバック）
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "当日固有の材料は確認できず（5パス確認済み）。"
    assert not any("factor が空" in w for w in vrq.check_ranking_warnings(doc))


# ── check3c [WARN]：自社決算の窓外帰属・連想の誤用（ローツェ〔6323〕型）─────
def test_self_earnings_out_of_window_warns(ranking_golden):
    # 自社決算を挙げるのに窓内に決算開示が無い＝決算が窓外（当日15:30以降＝翌日材料）で当日の
    # 日中要因にできない。「連想」はヘッジ語なので既存 check3 を免除してしまう（＝すり抜けた経路）が、
    # 本チェックはヘッジと独立に発火し、自社決算×連想買いの矛盾も同時に検出する。
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][1]["factor"] = "第1四半期決算（経常+51%）への好感が連想買いを呼び急伸。"
    broken["rows"][1]["disclosures"] = []
    warns = vrq.check_ranking_warnings(broken)
    assert any("自社決算を上昇要因に挙げる" in w for w in warns)
    assert any("自社決算が要因の行に「連想（買い）」" in w for w in warns)
    assert vrq.check_ranking(broken) == []   # WARN は非ブロッキング（exit code に影響しない）


def test_self_earnings_in_window_no_warn(ranking_golden):
    # 窓内に決算短信の開示が在れば（前営業日引け後∪当日15:30未満）自社決算帰属は正当＝無警告
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][0]["factor"] = "前営業日引け後の第1四半期決算（経常+51%）を好感した買い。"
    doc["rows"][0]["disclosures"] = [
        {"time": "15:30", "code": "4596", "name": "テスト製薬",
         "title": "2026年2月期 第1四半期決算短信〔日本基準〕（連結）",
         "pdf_url": "https://www.release.tdnet.info/inbs/140120260709000000.pdf",
         "origin": "prev", "date": "2026-07-02"}]
    assert not any("自社決算を上昇要因に挙げる" in w for w in vrq.check_ranking_warnings(doc))


def test_self_earnings_without_attribution_no_warn(ranking_golden):
    # 決算系語があっても帰属語（好感・材料視 等）との共起が無ければ発火しない（誤爆抑制）
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "経常増益基調の半導体装置株。当日固有の材料は確認できず。"
    assert not any("自社決算を上昇要因に挙げる" in w for w in vrq.check_ranking_warnings(doc))


def test_earnings_next_day_with_distant_uke_marker_no_warn(ranking_golden):
    # 「決算は引け後＝翌日材料」と正しく位置づけつつ、別文で「米株高を受け」等と書く正当な本文は
    # 誤検知しない（決算語と帰属語が同一文で近接しないため）。実際の 6323 訂正 factor の型。
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = (
        "前日の米半導体株高を受けた買い戻しに連れ高したとみられる。"
        "同社の第1四半期決算（経常+51%）は当日15:30の引け後開示＝翌営業日以降の材料である。")
    assert not any("自社決算を上昇要因に挙げる" in w for w in vrq.check_ranking_warnings(doc))


# ── 旧スキーマ（items）／構造破損 ─────────────────────────────────
def test_items_variant_emits_warn(ranking_golden):
    doc = copy.deepcopy(ranking_golden)
    doc["items"] = doc.pop("rows")
    assert any("旧スキーマ 'items'" in w for w in vrq.check_ranking_warnings(doc))


def test_items_variant_still_checks_rows(ranking_golden):
    # items フォールバックでも行検査は効く（開示タグ＋disclosures 空は C1）
    doc = copy.deepcopy(ranking_golden)
    doc["items"] = doc.pop("rows")
    doc["items"][0]["disclosures"] = []
    assert any("disclosures[] が空" in e for e in vrq.check_ranking(doc))


def test_no_rows_no_items_structural_error():
    errs = vrq.check_ranking({"session_date": "2026-07-03"})
    assert len(errs) == 1 and "rows も items も無い" in errs[0]


# ── CLI / 集約 ────────────────────────────────────────────────
def test_errors_aggregated(ranking_golden):
    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["disclosures"] = []                    # C1 その1
    broken["rows"][0]["factor_kind"] = "開示"
    broken["rows"][3]["factor_kind"] = "開示"                 # C1 その2（元テーマを開示化・disc 空）
    errs = vrq.check_ranking(broken)
    assert len(errs) >= 2


def test_cli_exit_codes(ranking_golden, tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(ranking_golden, ensure_ascii=False), encoding="utf-8")
    assert vrq.main([str(good)]) == 0

    broken = copy.deepcopy(ranking_golden)
    broken["rows"][0]["disclosures"] = []
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    assert vrq.main([str(bad)]) == 1
    assert vrq.main([str(good), str(bad)]) == 1      # 1本でも NG なら非ゼロ

    assert vrq.main([str(tmp_path / "missing.json")]) == 1
    capsys.readouterr()


def test_cli_warn_exit_zero(ranking_golden, tmp_path, capsys):
    doc = copy.deepcopy(ranking_golden)
    doc["rows"][1]["factor"] = "同社の材料がセクター物色に点火した。"   # WARN のみ
    p = tmp_path / "warn.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert vrq.main([str(p)]) == 0
    assert "WARN" in capsys.readouterr().err


# ── private evidence.v1 gate / machine repair ─────────────────
def _not_found_evidence(checks):
    return {
        "schema_version": "evidence.v1", "session_date": "2026-07-03",
        "items": [{"code": "3990", "status": "unresolved", "checks": checks}],
    }


def test_legacy_public_json_does_not_require_private_evidence(ranking_golden):
    assert vrq.check_ranking(ranking_golden) == []


def test_not_found_requires_all_five_passes_when_evidence_supplied(ranking_golden):
    evidence = _not_found_evidence({
        "disclosures": "done", "kabutan_news": "done", "web_search": "done",
        "sector_cluster": "done",
    })
    findings = vrq.audit_ranking(ranking_golden, evidence=evidence)
    item = next(f for f in findings if f["rule_id"] == "RANK_NOT_FOUND_FIVE_PASS_INCOMPLETE")
    assert item["code"] == "3990"
    assert item["severity"] == "ERROR"
    assert "edinet" in item["message"]


def test_not_found_accepts_done_na_or_unavailable_states(ranking_golden):
    evidence = _not_found_evidence({
        "disclosures": "done", "kabutan_news": {"status": "done"},
        "web_search": "done", "sector_cluster": "na", "edinet": "unavailable",
    })
    assert vrq.check_ranking(ranking_golden, evidence=evidence) == []


def test_ranking_machine_json_and_targeted_code(ranking_golden, tmp_path, capsys):
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(ranking_golden, ensure_ascii=False), encoding="utf-8")
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_not_found_evidence({}), ensure_ascii=False), encoding="utf-8")

    assert vrq.main([str(ranking_path), "--evidence", str(evidence_path),
                     "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    finding = next(item for item in payload["files"][0]["findings"]
                   if item["rule_id"] == "RANK_NOT_FOUND_FIVE_PASS_INCOMPLETE")
    assert set(finding) == {"code", "path", "rule_id", "severity", "message"}

    assert vrq.main([str(ranking_path), "--evidence", str(evidence_path),
                     "--repair-targets"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(target["code"] == "3990" for target in payload["files"][0]["targets"])

    target_path = tmp_path / "repair" / "ranking_targets.json"
    assert vrq.main([str(ranking_path), "--evidence", str(evidence_path),
                     "--repair-targets", str(target_path)]) == 1
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    assert any(target["code"] == "3990" for target in payload["files"][0]["targets"])
    assert capsys.readouterr().out == ""
