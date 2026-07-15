"""GitHub Pages 用 HTML と Gmail 本文 HTML の生成（東証 値上がり率ランキング）。

Gmail 本文（generate_email_html）のトンマナ・書式・カラーは PTS 版
（pts-ranking-monitor/scripts/html_generator.py）と**同一**。TSE 固有のデータ
（列＝PTS気配なし・終値(円)／抽出条件＝≥+5%・¥10M・100億・上位30社／出典）のみ差し替えている。

Pages SPA（generate_pages_html）は 2026-07 の「金融紙エディトリアル」リデザインで PTS 版から
**意図的に分岐**した（明色ペーパー基調・セリフ見出し（Noto Serif JP）・ヘアライン罫線・
タブラー数字。ランキング表本体・株探リンク・[開示PDF]・薄商い折りたたみ・モバイルのカード化の
挙動は従来どおり）。PTS 版と同期する際はメール HTML と共有ロジックのみを対象にする。

公開データ（docs/data/YYYY-MM-DD.json）は build_day_ranking.py の出力に各行の変動要因（factor /
factor_kind）を埋めたもの（rows に disclosures/pdf_url・counts・capped を含むフルデータ）。
時価総額は要件により **常に億円の整数（カンマ区切り、1兆円以上も億円表示）** とする。
"""
import re


def fmt_mcap(oku, flag=""):
    if oku is None:
        return "—"
    return f"{oku:,}{flag or ''}"


def fmt_pct(pct):
    if pct is None:
        return "—"
    return f"+{pct:.2f}%"


def _count_label(data):
    """capped のとき『該当 M 社（上位 N 社を掲載）』、それ以外は『該当 N 社』。"""
    rows = data.get("rows", [])
    n = len(rows)
    total = (data.get("counts", {}) or {}).get("qualifying", n)
    if data.get("capped"):
        return f"該当 {total} 社（上位 {n} 社を掲載）"
    return f"該当 {n} 社"


def _criteria_text(data):
    c = data.get("criteria", {}) or {}
    pct = c.get("min_pct", 5)
    tm = (c.get("min_turnover_yen", 10_000_000)) / 1e6
    mc = c.get("min_mcap_oku", 100)
    cap = c.get("max_rank")
    s = (f"値上がり率≥+{pct:g}% かつ 売買代金≥{tm:g}百万円／東証個別・時価総額≥{mc:g}億円")
    if cap:
        s += f"・上昇率上位{cap:g}社"
    return s


# ----------------------------------------------------------------------------- email

def _kind_badge(kind):
    k = (kind or "").strip("[]")
    color = {"開示": "#1b7f3b", "報道": "#1a6fd0", "テーマ": "#8a6d00"}.get(k, "#777")
    return (f'<span style="display:inline-block;font-size:10px;color:#fff;background:{color};'
            f'border-radius:3px;padding:1px 5px;margin-right:4px;white-space:nowrap;">{k or "—"}</span>') if k else ""


_FACTOR_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _factor_html(text):
    """factor 本文中の Markdown リンク [表示名](URL) を <a> に変換する（Pages SPA の mdInline と
    同じ描画）。リンクを含まない既存の factor は素通しで表示挙動は不変（vmq.MD_LINK_RE と同一パターン）。"""
    return _FACTOR_LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)


def generate_email_html(data, pages_url, max_items=25):
    rows = data.get("rows", [])
    display = rows[:max_items] if max_items else rows
    win = data.get("session_window", "")
    date_str = data.get("session_date", "")
    count_label = _count_label(data)
    trs = []
    for r in display:
        factor = _factor_html((r.get("factor") or "（材料未確認）").strip())
        badge = _kind_badge(r.get("factor_kind"))
        trs.append(f"""<tr>
          <td style="padding:7px 8px;border-bottom:1px solid #eee;text-align:right;font-family:Arial,sans-serif;">{r.get('rank','')}</td>
          <td style="padding:7px 8px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;white-space:nowrap;">{r.get('code','')}</td>
          <td style="padding:7px 8px;border-bottom:1px solid #eee;white-space:nowrap;">{r.get('name','')}</td>
          <td style="padding:7px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;font-family:Arial,sans-serif;">{fmt_mcap(r.get('mcap_oku'), r.get('mcap_flag'))}</td>
          <td style="padding:7px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;font-family:Arial,sans-serif;color:#c0392b;font-weight:600;">{fmt_pct(r.get('pct'))}</td>
          <td class="col-factor" style="padding:7px 8px;border-bottom:1px solid #eee;font-size:12px;line-height:1.5;">{badge}{factor}</td>
        </tr>""")
    table_rows = "\n".join(trs)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>@media screen and (max-width:600px){{ .col-factor{{display:none!important;}} }}</style>
</head>
<body style="font-family:'Helvetica Neue',Arial,'Hiragino Sans',sans-serif;color:#333;margin:0;padding:0;background:#f5f5f5;">
  <div style="max-width:980px;margin:20px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
    <div style="background:#11243f;color:#fff;padding:18px 22px;">
      <h1 style="margin:0;font-size:19px;font-weight:600;">📈 東証 値上がり率ランキング</h1>
      <p style="margin:6px 0 0;font-size:13px;opacity:0.9;">{date_str}｜{count_label}｜{win}</p>
      <p style="margin:4px 0 0;font-size:11px;opacity:0.7;">条件：{_criteria_text(data)}</p>
    </div>
    <div style="padding:16px 20px;">
      <div style="text-align:center;margin:0 0 14px;">
        <a href="{pages_url}" target="_blank" style="display:inline-block;background:#11243f;color:#fff;padding:9px 26px;border-radius:6px;text-decoration:none;font-size:14px;">全件・詳細を表示 →</a>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-size:13px;">
        <thead><tr style="background:#f6f8fa;">
          <th style="padding:8px;text-align:right;border-bottom:2px solid #11243f;">#</th>
          <th style="padding:8px;text-align:left;border-bottom:2px solid #11243f;white-space:nowrap;">コード</th>
          <th style="padding:8px;text-align:left;border-bottom:2px solid #11243f;">銘柄</th>
          <th style="padding:8px;text-align:right;border-bottom:2px solid #11243f;white-space:nowrap;">時価総額<br>(億円)</th>
          <th style="padding:8px;text-align:right;border-bottom:2px solid #11243f;white-space:nowrap;">上昇率</th>
          <th class="col-factor" style="padding:8px;text-align:left;border-bottom:2px solid #11243f;width:40%;">変動要因</th>
        </tr></thead>
        <tbody>
{table_rows}
        </tbody>
      </table>
      <p style="margin:14px 0 0;font-size:11px;color:#888;">価格・売買代金・終値・市場区分・時価総額＝J-Quants V2（新規上場の時価総額は Yahoo Finance JP）／開示＝TDnet。† は増資・自己株で株探最新株数と乖離。本情報は参考であり投資助言ではない。</p>
    </div>
    <div style="background:#f6f8fa;padding:11px 20px;font-size:11px;color:#999;text-align:center;">東証 値上がりランキング・モニター｜Claude 定期実行（自動送信）</div>
  </div>
</body></html>"""


# ----------------------------------------------------------------------------- pages

def _web_asset(name):
    """Return a UTF-8 web source asset bundled with the package."""
    from importlib.resources import files

    return files("tse_ranking_monitor.web").joinpath(name).read_text(encoding="utf-8")


def generate_pages_html():
    """Build the single-file Pages SPA from separated HTML/CSS/JavaScript sources."""
    # Source files conventionally end in LF; placeholders already own their
    # surrounding newlines.  Trimming only trailing LFs keeps the generated
    # document byte-identical to the pre-split single-file implementation.
    template = _web_asset("index.template.html").rstrip("\n")
    css = _web_asset("app.css").rstrip("\n")
    javascript = _web_asset("app.js").rstrip("\n")
    return template.replace("{{APP_CSS}}", css).replace("{{APP_JS}}", javascript)
