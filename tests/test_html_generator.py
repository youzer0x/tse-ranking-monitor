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
