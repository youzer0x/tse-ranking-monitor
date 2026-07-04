# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""時価総額データの取得 (Yahoo Finance JP)

J-Quants で取得できない新規上場銘柄 (末尾英字コード等) 用のフォールバック。
HTMLスクレイプで「時価総額」値を取得する。

正本は market-scripts-common（算出方式の出自は tdnet-monitor）。tdnet-monitor を含む各消費リポへは
sync.py で配布する。bs4/lxml が無い環境では正規表現のみで
フォールバック動作する（_parse_yahoo_market_cap_text を直接ページテキストに適用）。
"""

import re
import time
import requests

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def fetch_market_cap_yahoo(code: str) -> float | None:
    """1銘柄の時価総額を Yahoo Finance JP から取得 (億円単位)。

    失敗時は None を返す (例外は内部で握り潰す)。
    """
    url = f"https://finance.yahoo.co.jp/quote/{code}.T"

    for attempt in (1, 2):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                if attempt == 1:
                    time.sleep(2.0)
                    continue
                return None

            resp.encoding = "utf-8"

            if _HAS_BS4:
                soup = BeautifulSoup(resp.text, "html.parser")
                # 方法1: 「時価総額」を含む dt/th/span の隣接 dd/td/span
                for tag in soup.find_all(["dt", "th", "span"]):
                    if "時価総額" in tag.get_text(strip=True):
                        sib = tag.find_next(["dd", "td", "span"])
                        if sib:
                            val = _parse_yahoo_market_cap(sib.get_text(strip=True))
                            if val:
                                return val
                text = soup.get_text()
            else:
                text = re.sub(r"<[^>]+>", " ", resp.text)

            # 方法2: ページ全体テキストから正規表現
            val = _parse_yahoo_market_cap_text(text)
            if val:
                return val

        except Exception:
            if attempt == 1:
                time.sleep(2.0)
                continue
            return None

    return None


def _parse_yahoo_market_cap(text: str) -> float | None:
    """時価総額表記をパースして億円単位で返す。
    Yahoo は通常「百万円」「億円」「兆円」表記。"""
    t = text.replace(",", "").replace(" ", "").replace("　", "").strip()

    m = re.search(r"([\d.]+)\s*兆\s*([\d.]*)\s*億?", t)
    if m:
        cho = float(m.group(1))
        oku = float(m.group(2)) if m.group(2) else 0
        return round(cho * 10000 + oku, 1)

    m = re.search(r"([\d.]+)\s*億", t)
    if m:
        return round(float(m.group(1)), 1)

    m = re.search(r"([\d.]+)\s*百万", t)
    if m:
        return round(float(m.group(1)) / 100, 1)

    return None


def _parse_yahoo_market_cap_text(text: str) -> float | None:
    """ページ全体テキストから抽出"""
    patterns = [
        r"時価総額[^\d]{0,30}?([\d,.]+)\s*兆\s*([\d,.]*)\s*億",
        r"時価総額[^\d]{0,30}?([\d,.]+)\s*兆",
        r"時価総額[^\d]{0,30}?([\d,.]+)\s*億",
        r"時価総額[^\d]{0,30}?([\d,.]+)\s*百万",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, text)
        if not m:
            continue
        v1 = float(m.group(1).replace(",", ""))
        if i == 0:
            v2 = float(m.group(2).replace(",", "")) if m.group(2) else 0
            return round(v1 * 10000 + v2, 1)
        elif i == 1:
            return round(v1 * 10000, 1)
        elif i == 2:
            return round(v1, 1)
        elif i == 3:
            return round(v1 / 100, 1)
    return None
