"""株探（かぶたん）個別銘柄ページの最新発行済株式数の取得。

本 skill（東証日中ランキング）では **`kabutan_shares()` のみ使用**する（† クロスチェック用）。
PTS 気配ランキングの取得関数（fetch_gainers/parse_html 等）も origin の互換のため同梱するが、
日中版では用いない（価格・売買代金・上昇率は J-Quants 由来で完結する）。

origin: pts-ranking-digest/scripts/kabutan_pts.py。stdlib のみ（urllib）。
"""
import sys, re, json, time, urllib.request, urllib.parse

BASE = "https://kabutan.jp/warning/pts_night_price_increase"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_SHARES = re.compile(r'発行済株式数.{0,40}?>([\d,]+)', re.S)


def kabutan_shares(code):
    """株探の個別銘柄ページから最新の発行済株式数（int）を取得。失敗時 None。

    J-Quants の ShOutFY（期末）は期中の増資・自己株消却を反映しないため、
    最新株数との乖離が大きい銘柄に「†」を付すクロスチェック用。
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = _SHARES.search(html)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    code = sys.argv[1] if len(sys.argv) > 1 else "7203"
    print(f"{code}: kabutan_shares={kabutan_shares(code)}")
