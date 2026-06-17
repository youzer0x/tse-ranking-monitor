"""東証 日中ランキングの publish（GitHub Pages JSON/SPA ＋ Gmail メール本文の生成・送信）。

入力：build_day_ranking.py の出力 JSON に、Claude が各 row の factor/factor_kind を埋めたもの。
出力：
  - docs/data/<session_date>.json（ランキング＋変動要因）／docs/data/manifest.json／30日より古い JSON 削除
  - docs/index.html（日付選択式の Pages・ランキング表）
  - メール HTML（--send 指定で Gmail API〔HTTPS〕送信＝gmail_sender.send_gmail）

Pages の体裁は project-private/tdnet-monitor を下敷き。メール送信は PTS 版と同じ
Gmail API 方式（クラウドは SMTP 不可。gmail_sender.py 参照）。本体は stdlib のみ。

usage:
  python publish.py --in ranking.json --docs ../docs [--send]
"""
import os, sys, json, glob, html, argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fmt_mcap(v):
    if v is None:
        return "—"
    if v >= 10000:
        return f"{v/10000:.1f}兆円"
    return f"{v:,.0f}億円"


def save_daily_json(data, docs_dir):
    data_dir = os.path.join(docs_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    sd = data["session_date"]
    counts = data.get("counts", {})
    ranked = len(data.get("rows", []))
    rec = {
        "date": sd,
        "session_window": data.get("session_window", ""),
        "generated_at": data.get("generated_at", ""),
        "criteria": data.get("criteria", {}),
        "count": ranked,                                  # 掲載数（=rows）
        "count_total": counts.get("qualifying", ranked),  # 条件該当の総数
        "capped": bool(data.get("capped")),
        "max_rank": (data.get("criteria") or {}).get("max_rank"),
        "items": [{
            "rank": r["rank"], "code": r["code"], "name": r["name"], "market": r.get("market"),
            "mcap_oku": r["mcap_oku"], "mcap_flag": r.get("mcap_flag", ""),
            "mcap_source": r.get("mcap_source"), "pct": r["pct"], "close": r.get("close"),
            "turnover_m": r.get("turnover_m"), "factor": r.get("factor", ""),
            "factor_kind": r.get("factor_kind", ""),
        } for r in data.get("rows", [])],
        "dropped_turnover": data.get("dropped_turnover", []),
        "dropped_mcap": data.get("dropped_mcap", []),
    }
    path = os.path.join(data_dir, f"{sd}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print(f"  daily json: {path} ({rec['count']} items)")
    return rec


def cleanup_old(docs_dir, keep_days=30):
    data_dir = os.path.join(docs_dir, "data")
    cutoff = date.today() - timedelta(days=keep_days)
    for fn in glob.glob(os.path.join(data_dir, "*.json")):
        base = os.path.basename(fn)
        if base == "manifest.json":
            continue
        try:
            if date.fromisoformat(base[:-5]) < cutoff:
                os.remove(fn)
        except ValueError:
            pass


def update_manifest(docs_dir):
    data_dir = os.path.join(docs_dir, "data")
    dates = []
    for fn in sorted(glob.glob(os.path.join(data_dir, "*.json")), reverse=True):
        base = os.path.basename(fn)
        if base == "manifest.json":
            continue
        try:
            dates.append(date.fromisoformat(base[:-5]).isoformat())
        except ValueError:
            pass
    with open(os.path.join(data_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": dates}, f, ensure_ascii=False)
    return dates


def write_pages_html(docs_dir):
    """日付選択式の Pages SPA（data/<date>.json を fetch して描画）。"""
    htmlsrc = r"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>東証 日中 値上がりランキング</title>
<style>
:root{--bg:#f0f2f5;--card:#fff;--primary:#0b5d2e;--accent:#ff6d00;--text:#1b2b22;--sub:#6b7d72;--border:#e0e4e8;--hover:#f2f8f4}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Hiragino Sans','Noto Sans JP',sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
.header{background:linear-gradient(135deg,#0b5d2e,#1f8a4c);color:#fff;padding:24px 20px}
.header-inner{max-width:1200px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.header h1{font-size:20px}.header-sub{font-size:12px;opacity:.85;margin-top:4px}
.date-selector select{padding:7px 12px;border-radius:6px;border:1px solid rgba(255,255,255,.4);background:rgba(255,255,255,.15);color:#fff;font-size:14px}
.summary{max-width:1200px;margin:14px auto 0;padding:0 14px;display:flex;gap:10px;flex-wrap:wrap}
.chip{background:var(--card);border-radius:8px;padding:7px 14px;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.chip .num{font-weight:700;color:var(--primary);font-size:15px}
.container{max-width:1200px;margin:14px auto 24px;padding:0 14px}
.card{background:var(--card);border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.toolbar{padding:10px 14px;border-bottom:1px solid var(--border)}
.toolbar input{padding:7px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;width:260px;max-width:100%}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{padding:9px 10px;text-align:left;background:#f4f8f5;border-bottom:2px solid var(--primary);font-size:12px;color:var(--sub);white-space:nowrap}
th.r,td.r{text-align:right}
tbody td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
tbody tr:hover td{background:var(--hover)}
.code{font-family:monospace;white-space:nowrap}.pct{font-weight:700;color:#c0392b;white-space:nowrap}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:#eef;color:#345;white-space:nowrap}
.tag.k開示{background:#e6f4ea;color:#0b5d2e}.tag.k報道{background:#fdeee0;color:#a65b00}.tag.kテーマ{background:#eef0f5;color:#445}
.footer{text-align:center;padding:18px;font-size:11px;color:var(--sub)}
.empty,.loading{text-align:center;padding:50px;color:var(--sub)}
@media(max-width:768px){.note-col{display:none}}
</style></head><body>
<div class="header"><div class="header-inner">
<div><h1>📈 東証 日中 値上がりランキング</h1><div class="header-sub">値上がり率≥+5%・売買代金≥¥10M・時価総額≥100億・東証個別</div></div>
<div class="date-selector"><label>セッション日: <select id="dateSelect" onchange="loadDate(this.value)"><option>読み込み中...</option></select></label></div>
</div></div>
<div class="summary" id="summary"></div>
<div class="container"><div class="card">
<div class="toolbar"><input id="q" type="text" placeholder="コード・銘柄で絞り込み..." oninput="render()"></div>
<div id="tableArea"><div class="loading">読み込み中...</div></div>
</div></div>
<div class="footer">東証 日中 値上がりランキング｜自動生成｜直近30日分を保持<br>本情報は参考であり投資助言ではない。</div>
<script>
let cur=null;
function fmtMcap(v){if(v==null)return'—';if(v>=10000)return(v/10000).toFixed(1)+'兆円';return v.toLocaleString('ja-JP')+'億円';}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
async function init(){try{const m=await(await fetch('data/manifest.json?'+Date.now())).json();const sel=document.getElementById('dateSelect');sel.innerHTML='';if(!m.dates.length){sel.innerHTML='<option>データなし</option>';document.getElementById('tableArea').innerHTML='<div class="empty">まだデータがありません。</div>';return;}m.dates.forEach((d,i)=>{const o=document.createElement('option');o.value=d;const dt=new Date(d+'T00:00:00');o.textContent=d+' ('+['日','月','火','水','木','金','土'][dt.getDay()]+')';if(i===0)o.selected=true;sel.appendChild(o);});loadDate(m.dates[0]);}catch(e){document.getElementById('tableArea').innerHTML='<div class="empty">読み込みに失敗しました。</div>';}}
async function loadDate(d){if(!d)return;document.getElementById('tableArea').innerHTML='<div class="loading">読み込み中...</div>';try{cur=await(await fetch('data/'+d+'.json?'+Date.now())).json();render();}catch(e){document.getElementById('tableArea').innerHTML='<div class="empty">この日付を読み込めませんでした。</div>';}}
function render(){if(!cur)return;const q=document.getElementById('q').value.toLowerCase();let items=(cur.items||[]).filter(it=>!q||it.code.toLowerCase().includes(q)||(it.name||'').toLowerCase().includes(q));
const tot=(cur.count_total!=null?cur.count_total:cur.count)||0;const chip=cur.capped?('<span class="num">'+tot+'</span> 社該当（上位 '+(cur.count||0)+' 社を掲載）'):('<span class="num">'+tot+'</span> 社該当');
document.getElementById('summary').innerHTML='<div class="chip">'+chip+'</div>'+'<div class="chip">'+esc(cur.session_window||'')+'</div>';
if(!items.length){document.getElementById('tableArea').innerHTML='<div class="empty">該当なし</div>';return;}
let h='<table><thead><tr><th class="r">順位</th><th>コード</th><th>銘柄</th><th>市場</th><th class="r">時価総額</th><th class="r">上昇率</th><th class="r">終値</th><th class="r">売買代金(百万)</th><th>変動要因</th><th>区分</th></tr></thead><tbody>';
items.forEach(it=>{const kind=(it.factor_kind||'').replace(/[\[\]]/g,'');h+='<tr><td class="r">'+it.rank+'</td><td class="code">'+esc(it.code)+'</td><td>'+esc(it.name)+(it.mcap_flag?' '+it.mcap_flag:'')+'</td><td>'+esc(it.market||'')+'</td><td class="r">'+fmtMcap(it.mcap_oku)+'</td><td class="r pct">+'+Number(it.pct).toFixed(2)+'%</td><td class="r">'+(it.close!=null?Number(it.close).toLocaleString('ja-JP'):'—')+'</td><td class="r">'+(it.turnover_m!=null?Number(it.turnover_m).toLocaleString('ja-JP'):'—')+'</td><td class="note-col">'+esc(it.factor||'')+'</td><td>'+(kind?'<span class="tag k'+kind+'">'+kind+'</span>':'')+'</td></tr>';});
h+='</tbody></table>';document.getElementById('tableArea').innerHTML=h;}
init();
</script></body></html>"""
    path = os.path.join(docs_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(htmlsrc)
    print(f"  pages html: {path}")


def build_email_html(rec, pages_url, top=20):
    rows = []
    for it in rec["items"][:top]:
        kind = (it.get("factor_kind") or "").replace("[", "").replace("]", "")
        rows.append(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right">{it["rank"]}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;font-family:monospace">{html.escape(it["code"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{html.escape(it["name"] or "")}{(" "+it["mcap_flag"]) if it.get("mcap_flag") else ""}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right">{_fmt_mcap(it["mcap_oku"])}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;color:#c0392b;font-weight:700">+{it["pct"]:.2f}%</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{html.escape(it.get("factor") or "")} '
            f'<span style="color:#888">{html.escape(kind)}</span></td></tr>')
    total = rec.get("count_total", rec["count"])
    head_cnt = (f"{total}社該当（上位{rec['count']}社を掲載）" if rec.get("capped") else f"{rec['count']}社該当")
    trunc = ("<p style='font-size:12px;color:#666'>※ メールは上位%d社を表示。掲載全%d社は Web を参照。</p>"
             % (top, rec["count"])) if rec["count"] > top else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:'Hiragino Sans',sans-serif;color:#333;background:#f5f5f5;margin:0;padding:0">
<div style="max-width:900px;margin:16px auto;background:#fff;border-radius:8px;overflow:hidden">
<div style="background:#0b5d2e;color:#fff;padding:18px 22px"><h1 style="margin:0;font-size:18px">📈 東証 日中 値上がりランキング</h1>
<p style="margin:6px 0 0;font-size:13px;opacity:.9">{html.escape(rec.get("session_window",""))}｜{head_cnt}</p></div>
<div style="padding:16px 22px">
<p style="margin:0 0 12px;font-size:13px;color:#555">抽出条件：値上がり率≥+5%・売買代金≥¥10M・時価総額≥100億・東証個別株のみ。</p>
<div style="text-align:center;margin:0 0 14px"><a href="{pages_url}" style="display:inline-block;background:#0b5d2e;color:#fff;padding:9px 24px;border-radius:6px;text-decoration:none;font-size:14px">全件・変動要因を表示 →</a></div>
<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="background:#f4f8f5">
<th style="padding:7px 8px;text-align:right;border-bottom:2px solid #0b5d2e">順位</th>
<th style="padding:7px 8px;text-align:left;border-bottom:2px solid #0b5d2e">コード</th>
<th style="padding:7px 8px;text-align:left;border-bottom:2px solid #0b5d2e">銘柄</th>
<th style="padding:7px 8px;text-align:right;border-bottom:2px solid #0b5d2e">時価総額</th>
<th style="padding:7px 8px;text-align:right;border-bottom:2px solid #0b5d2e">上昇率</th>
<th style="padding:7px 8px;text-align:left;border-bottom:2px solid #0b5d2e">変動要因</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>{trunc}
</div>
<div style="background:#f8f9fa;padding:10px 22px;font-size:11px;color:#999;text-align:center">東証日中ランキング｜自動送信｜本情報は参考であり投資助言ではない</div>
</div></body></html>"""


def send_email(html_body, rec):
    """Gmail API（HTTPS）でメール送信する（PTS 版と同方式）。

    クラウドルーチン環境は SMTP(465) を通さないため、OAuth2 リフレッシュトークン
    （GMAIL_CLIENT_ID/GMAIL_CLIENT_SECRET/GMAIL_REFRESH_TOKEN）で Gmail API を叩く。
    認証情報が無ければ送信をスキップする。
    """
    if not (os.environ.get("GMAIL_CLIENT_ID") and os.environ.get("GMAIL_CLIENT_SECRET")
            and os.environ.get("GMAIL_REFRESH_TOKEN") and os.environ.get("GMAIL_ADDRESS")):
        print("  (skip send: GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN / GMAIL_ADDRESS 未設定)")
        return False
    import gmail_sender
    return gmail_sender.send_gmail(
        html_body, rec["date"], rec["count"],
        total=rec.get("count_total"), capped=rec.get("capped", False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="build_day_ranking.py の出力 JSON（要因記入済み）")
    ap.add_argument("--docs", default="docs", help="GitHub Pages の docs ディレクトリ")
    ap.add_argument("--pages-url", default=os.environ.get("PAGES_URL", "./"))
    ap.add_argument("--send", action="store_true", help="Gmail 送信（環境変数が揃っていれば）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(args.inp, encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(os.path.join(args.docs, "data"), exist_ok=True)
    rec = save_daily_json(data, args.docs)
    cleanup_old(args.docs, keep_days=30)
    update_manifest(args.docs)
    write_pages_html(args.docs)
    email_html = build_email_html(rec, args.pages_url)
    email_path = os.path.join(args.docs, "data", f"{rec['date']}_email.html")
    with open(email_path, "w", encoding="utf-8") as f:
        f.write(email_html)
    print(f"  email html: {email_path}")
    if args.send:
        send_email(email_html, rec)


if __name__ == "__main__":
    main()
