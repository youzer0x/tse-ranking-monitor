# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""TDnet 適時開示情報の取得（一次情報）。

ソース: https://www.release.tdnet.info/inbs/I_list_{NNN}_{YYYYMMDD}.html
  - 1ページ100件・NNN=001,002,... のページネーション。UTF-8。
  - 行は kjTime / kjCode(5桁) / kjName / kjTitle(<a href=...pdf>) / kjPlace。
  - 開示一覧は当日を含め概ね31日分のみ公開（過去日は取得不可になりうる）。

東証日中（9:00–15:30）の値上がりの材料候補は、市場が反応できた開示＝
  「前営業日 15:30（前回引け後）〜 当日 15:30 直前（当日引け）」の窓。
  - 前営業日 15:30 ちょうど以降：後場終了後の開示で翌日（＝当日）寄りに織り込まれる → 含める。
  - 当日 15:30 ちょうど：後場終了後で日中に反応不能（今夜の PTS 材料）→ 除外（厳密に < 15:30）。
  → disclosures_window()。PTS ナイト要因候補の突合には disclosures_by_code()（単日・引け後）を使う。

データ供給は **自前取得**（隣接2営業日のみ・軽量）で自己完結する（tdnet-monitor の
docs/data には依存しない）。stdlib のみ（urllib + 正規表現、bs4 不要）。
"""
import re, time, urllib.request
from datetime import date

BASE = "https://www.release.tdnet.info/inbs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_ROW = re.compile(
    r'kjTime"[^>]*>\s*(\d{2}:\d{2})\s*</td>.*?'
    r'kjCode"[^>]*>\s*([0-9A-Za-z]+)\s*</td>.*?'
    r'kjName"[^>]*>\s*(.*?)\s*</td>.*?'
    r'kjTitle"[^>]*>(.*?)</td>',
    re.S,
)


def _code4(code5):
    code5 = code5.strip()
    if len(code5) == 5 and code5.endswith("0"):
        return code5[:-1]
    return code5


def _parse(html):
    rows = []
    for m in _ROW.finditer(html):
        t, code5, name, title_cell = m.groups()
        a = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', title_cell, re.S)
        if a:
            href, title = a.group(1), re.sub(r"<.*?>", "", a.group(2)).strip()
            pdf = href if href.startswith("http") else f"{BASE}/{href}"
        else:
            href, title, pdf = "", re.sub(r"<.*?>", "", title_cell).strip(), ""
        rows.append(dict(time=t, code=_code4(code5),
                         name=re.sub(r"<.*?>", "", name).strip(),
                         title=title, pdf_url=pdf))
    return rows


def fetch_disclosures(target, max_pages=30):
    """target（date or 'YYYY-MM-DD'）の適時開示一覧を全件取得する。"""
    if isinstance(target, date):
        ymd = target.strftime("%Y%m%d")
    else:
        ymd = str(target).replace("-", "")
    out = []
    for page in range(1, max_pages + 1):
        url = f"{BASE}/I_list_{page:03d}_{ymd}.html"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status != 200:
                    break
                html = r.read().decode("utf-8", "replace")
        except Exception:
            break
        rows = _parse(html)
        if not rows:
            break
        out.extend(rows)
        time.sleep(0.2)
    return out


def disclosures_by_code(target, since_hhmm="15:30"):
    """{4桁コード: [開示...]}（since_hhmm 以降のみ・単日）。PTSナイト要因候補の突合に使う。"""
    by = {}
    for d in fetch_disclosures(target):
        if d["time"] >= since_hhmm:
            by.setdefault(d["code"], []).append(d)
    for v in by.values():
        v.sort(key=lambda x: x["time"])
    return by


def disclosures_window(prev_target, session_target, close_hhmm="15:30"):
    """東証日中の値上がり材料候補：前回引け後〜当日引け直前の開示を {4桁コード:[...]} で返す。

    窓（厳密）：前営業日 time >= close_hhmm（15:30 ちょうども含む） ∪ 当日 time < close_hhmm。
    各開示には origin（'prev'/'day'）と date を付して、どちらの日のどの時刻かを残す。
    """
    by = {}

    def _ymd(t):
        return t.strftime("%Y-%m-%d") if isinstance(t, date) else str(t)

    prev_ymd, day_ymd = _ymd(prev_target), _ymd(session_target)
    for d in fetch_disclosures(prev_target):
        if d["time"] >= close_hhmm:
            d2 = dict(d, origin="prev", date=prev_ymd)
            by.setdefault(d["code"], []).append(d2)
    for d in fetch_disclosures(session_target):
        if d["time"] < close_hhmm:
            d2 = dict(d, origin="day", date=day_ymd)
            by.setdefault(d["code"], []).append(d2)
    for v in by.values():
        v.sort(key=lambda x: (x["date"], x["time"]))
    return by


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 2:
        by = disclosures_window(sys.argv[1], sys.argv[2])
        n = sum(len(v) for v in by.values())
        print(f"# window {sys.argv[1]}>=15:30 ∪ {sys.argv[2]}<15:30: "
              f"{len(by)} codes ({n} items)")
    else:
        tgt = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
        by = disclosures_by_code(tgt)
        n = sum(len(v) for v in by.values())
        print(f"# {tgt}: {len(by)} codes with disclosures >=15:30 ({n} items)")
    for code in sorted(by):
        for d in by[code]:
            print(f"{d.get('date','')}\t{d['time']}\t{d['code']}\t{d['name'][:16]}\t{d['title'][:48]}")
