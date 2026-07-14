"""html_generator.py の書式関数とメール HTML 生成の単体テスト（純粋変換・ネット非接触）。"""
import html_generator as hg


# ── 書式関数 ──────────────────────────────────────────────
def test_fmt_mcap():
    assert hg.fmt_mcap(None) == "—"
    assert hg.fmt_mcap(1234) == "1,234"
    assert hg.fmt_mcap(1234, "†") == "1,234†"   # 株数乖離フラグ付き


def test_fmt_pct():
    assert hg.fmt_pct(None) == "—"
    assert hg.fmt_pct(15.09) == "+15.09%"
    assert hg.fmt_pct(5.0) == "+5.00%"


# ── generate_email_html ──────────────────────────────────────
def _data(n_rows):
    return {
        "session_date": "2026-07-03",
        "session_window": "2026-07-03 09:00–15:30 JST",
        "criteria": {"min_pct": 5, "min_turnover_yen": 10_000_000, "min_mcap_oku": 100, "max_rank": 50},
        "counts": {"qualifying": n_rows},
        "rows": [
            {"rank": i + 1, "code": f"700{i}", "name": f"テスト銘柄{i}",
             "mcap_oku": 1000 + i, "mcap_flag": "", "pct": 5.0 + i,
             "factor": f"材料{i}", "factor_kind": "開示"}
            for i in range(n_rows)
        ],
    }


def test_generate_email_html_contains_stock_names():
    html = hg.generate_email_html(_data(3), "https://example.github.io/x/")
    assert "テスト銘柄0" in html and "テスト銘柄2" in html
    assert "https://example.github.io/x/" in html   # 詳細リンク
    assert "東証 値上がり率ランキング" in html


def test_generate_email_html_respects_max_items():
    # 5行のうち max_items=2 → 先頭2行だけ描画され、3行目以降は出ない
    html = hg.generate_email_html(_data(5), "https://x/", max_items=2)
    assert "テスト銘柄0" in html and "テスト銘柄1" in html
    assert "テスト銘柄2" not in html and "テスト銘柄4" not in html


def test_generate_email_html_handles_empty_rows():
    # 0件でも例外を投げずに HTML を返す（配信が落ちない）
    html = hg.generate_email_html(_data(0), "https://x/")
    assert "東証 値上がり率ランキング" in html


# ── factor 内 Markdown リンクの描画（market タブ mdInline と同じ挙動）────
def test_factor_html_converts_markdown_link():
    out = hg._factor_html("格上げを好感（[日経](https://www.nikkei.com/article/x)）。")
    assert '<a href="https://www.nikkei.com/article/x" target="_blank" rel="noopener">日経</a>' in out
    assert "[日経]" not in out


def test_factor_html_passes_plain_text_unchanged():
    # リンクを含まない既存の factor は素通し（表示挙動は不変）
    assert hg._factor_html("前日引け後の開示を好感し大幅高。") == "前日引け後の開示を好感し大幅高。"


def test_email_renders_factor_markdown_link():
    data = _data(1)
    data["rows"][0]["factor"] = "格上げ（[日経](https://www.nikkei.com/article/x)）。"
    html = hg.generate_email_html(data, "https://x/")
    assert '<a href="https://www.nikkei.com/article/x"' in html


# ── generate_pages_html（SPA・2026-07-14 改修のレイアウト仕様）──────────
# SPA は静的文字列1本なので、仕様が消えた/入ったことを文字列で検査する（番犬）。
def test_pages_ranking_tab_has_no_market_strip():
    # ランキングタブ冒頭の市況サマリー帯（mstrip）は全カット。ヘッダー直下は該当者数から。
    html = hg.generate_pages_html()
    assert "marketStrip" not in html
    assert "mstrip" not in html
    assert "renderStrip" not in html


def test_pages_header_has_no_gray_hairline_under_rule():
    # ヘッダー下部の青系ライン直下のグレー薄線（.header::after）は廃止。
    html = hg.generate_pages_html()
    assert ".header::after" not in html
    assert "border-bottom:1px solid var(--rule)" in html   # 青系ラインは維持


def test_pages_sector_table_single_metric_with_driver_column():
    # セクター騰落率は加重のみ・「銘柄」（主導銘柄）カラムを33業種の右に新設。注釈・⚠タグは廃止。
    html = hg.generate_pages_html()
    assert "<th>33業種</th><th>銘柄</th>" in html
    assert "単純平均" not in html
    assert "中央値" not in html
    assert "sector_notes" not in html
    assert "mtag" not in html
    assert "s.drivers" in html and "drvname" in html   # drivers 欠落（過去JSON）は「—」表示
    assert "drvrow" in html   # 複数銘柄該当時は1銘柄=1行で併記
    assert "text-overflow" not in html   # 銘柄名の省略（ellipsis）は厳禁＝常に全文表示


def test_pages_has_no_bought_sold_sections():
    # 「買われた/売られたセクター・テーマ」セクションは全カット（テーマ別資金フローは存続）。
    html = hg.generate_pages_html()
    assert "買われたセクター" not in html
    assert "売られたセクター" not in html
    assert "sideSection" not in html
    assert "テーマ別の資金フロー" in html


def test_pages_meta_moved_into_info_modal():
    # 対象日時・生成日時・抽出条件はヘッダー直下から撤去し、該当社数横の「データ情報」
    # ボタンからモーダル表示（2026-07-14 改修）。該当社数チップ（capped 分岐含む）は維持。
    html = hg.generate_pages_html()
    assert 'id="infoModal"' in html and 'id="infoBody"' in html
    assert "openInfo" in html and "showModal" in html
    assert 'id="note"' not in html               # 抽出条件の常時表示を廃止
    assert 'chip">生成 ' not in html             # 生成日時チップを廃止
    assert "session_window" in html              # モーダル側で継続表示
    assert "社該当（上位 " in html               # capped チップの分岐が残っている
    # 画面中央に表示（CSSリセット *{margin:0} が dialog の margin:auto を打ち消すため明示指定）
    assert "dialog.info{position:fixed;inset:0;margin:auto;" in html
