# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""時価総額データの取得 (J-Quants V2 API)

J-Quants V2 のバリュエーション指標 API (/equities/valuation) の MktCap を取得し、億円に換算する。
MktCap が無い銘柄 (新規上場後、最初の決算短信前等) は market_cap_yahoo に委譲する。

取得方式（要点）:
  時価総額(億円) = MktCap(百万円) / 100（小数1桁に丸め）
  - MktCap は J-Quants が「当日終値 × 自己株式を控除した株式数」で算出した値（分割・併合対応済）。
    発行済株式数ベースの時価総額より、自己株式相当分だけ小さくなる。
  - 全銘柄を日付指定で一括取得し、当日に無ければ最大 LOOKBACK_DAYS 日さかのぼる。
  - 東証本則の判定は bars/daily の収録有無で行う（fetch_tse_codes / compute_one）。
  - MktCap が null の東証銘柄は Yahoo Finance JP にフォールバック。valuation API 自体の
    失敗時はフォールバックしない（全銘柄スクレイプを避け、呼び出し側のキャッシュ等に任せる）。

正本は market-scripts-common。tdnet-monitor を含む各消費リポへは sync.py で配布する。
Yahoo インポートは遅延（フォールバック時のみ）。
"""

import os
import time
import requests
from datetime import date, timedelta
from collections import Counter


BASE = "https://api.jquants.com/v2"
TIMEOUT = 20
MAX_RETRY = 5
LOOKBACK_DAYS = 5

# (prices, price_date) を target_date ごとにキャッシュ。
_PRICES_CACHE: dict[date, tuple[dict[str, float], date]] = {}
# ({Code(5桁): 時価総額(億円)}, 採用日) を target_date ごとにキャッシュ。
_VALUATION_CACHE: dict[date, tuple[dict[str, float], date]] = {}


def _request(api_key: str, path: str, params: dict) -> list[dict]:
    """V2 API を呼び、pagination_key を自動連結して data 配列を返す。"""
    headers = {"x-api-key": api_key}
    out: list[dict] = []
    page_key: str | None = None
    for _ in range(50):
        p = dict(params)
        if page_key:
            p["pagination_key"] = page_key
        body: dict | None = None
        for attempt in range(MAX_RETRY):
            try:
                r = requests.get(f"{BASE}{path}", headers=headers, params=p, timeout=TIMEOUT)
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                body = r.json()
                break
            except requests.RequestException:
                if attempt == MAX_RETRY - 1:
                    raise
                time.sleep(1.5 ** attempt)
        if body is None:
            break
        out.extend(body.get("data", []))
        page_key = body.get("pagination_key")
        if not page_key:
            break
    return out


def _fetch_close_prices(api_key: str, target_date: date) -> tuple[dict[str, float], date]:
    """target_date から最大 LOOKBACK_DAYS 遡って終値が得られる日のデータを返す。
    返り値: ({Code(5桁): AdjC}, 採用日)
    """
    if target_date in _PRICES_CACHE:
        return _PRICES_CACHE[target_date]
    for back in range(LOOKBACK_DAYS + 1):
        d = target_date - timedelta(days=back)
        rows = _request(api_key, "/equities/bars/daily", {"date": d.isoformat()})
        prices = {r["Code"]: r["AdjC"] for r in rows if r.get("AdjC") is not None}
        if prices:
            _PRICES_CACHE[target_date] = (prices, d)
            return prices, d
    return {}, target_date


def prime_price_cache(target_date: date, prices: dict[str, float]) -> None:
    """既に取得済みの当日 {Code(5桁): AdjC} をキャッシュに流し込み、再取得を避ける。

    build_day_ranking.py は bars/daily を別経路で取得済みのため、その AdjC を再利用する。
    """
    if prices:
        _PRICES_CACHE[target_date] = (dict(prices), target_date)


def _mktcap_oku(million_yen: float | None) -> float | None:
    """valuation API の MktCap（百万円）を億円（小数1桁）に換算する。"""
    if million_yen is None:
        return None
    return round(float(million_yen) / 100, 1)


def _fetch_valuation_mktcaps(api_key: str, target_date: date) -> tuple[dict[str, float], date]:
    """target_date から最大 LOOKBACK_DAYS 遡って MktCap が得られる日の全銘柄分を返す。
    返り値: ({Code(5桁): 時価総額(億円)}, 採用日)。MktCap が null の行は含めない。
    全日空でも ({}, target_date) をキャッシュし、同一実行内で再取得しない。
    """
    if target_date in _VALUATION_CACHE:
        return _VALUATION_CACHE[target_date]
    for back in range(LOOKBACK_DAYS + 1):
        d = target_date - timedelta(days=back)
        rows = _request(api_key, "/equities/valuation", {"date": d.isoformat()})
        mcaps = {r["Code"]: _mktcap_oku(r["MktCap"]) for r in rows if r.get("MktCap") is not None}
        if mcaps:
            if d != target_date:
                print(f"  WARN: valuation MktCap for {target_date} not available; using {d} "
                      "(previous close basis)")
            _VALUATION_CACHE[target_date] = (mcaps, d)
            return mcaps, d
    _VALUATION_CACHE[target_date] = ({}, target_date)
    return {}, target_date


def _normalize_code(code5: str) -> str:
    """J-Quants の 5 桁コードを TDnet 表記 (4 桁または末尾英字) に正規化。

    5 桁コードは「4 桁の証券コード＋予約桁 0」。証券コードは数字 4 桁、または新方式の
    英数字 4 桁（数字 3 桁＋英字 1 桁、例 285A → J-Quants では 285A0）。末尾 0 を一律で
    外す（jquants.code4 と同一ロジック。tdnet-monitor には jquants.py を配布しないため複製）。
    25935 のように末尾が 0 でない 5 桁コード（優先株等）はそのまま返す。
    """
    c = str(code5)
    if len(c) == 5 and c.endswith("0") and c[:4].isalnum():
        return c[:-1]
    return c


def fetch_tse_codes(target_date: date) -> set[str]:
    """東証本則 (プライム/スタンダード/グロース) の銘柄コード (4 桁) セットを返す。"""
    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        return set()
    try:
        prices, _ = _fetch_close_prices(api_key, target_date)
    except Exception as e:
        print(f"  !!! fetch_tse_codes failed: {type(e).__name__}: {e}")
        return set()
    return {_normalize_code(c) for c in prices.keys()}


def compute_one(api_key: str, code4: str, prices: dict[str, float], price_date: date):
    """1銘柄の時価総額(億円)を返す。

    返り値: (mcap_oku|None, shoutfy, period_end, corr, source)
      shoutfy / period_end は常に None、corr は常に 1.0（旧・自前算出方式との戻り値互換のため残置）。
      source ∈ {"jquants", "yahoo", "skipped_non_tse", None}
      - "jquants": valuation API の MktCap（自己株式控除後の株数ベース）
      - "yahoo"  : MktCap が null（新規上場等）→ Yahoo フォールバック
      - None     : 取得失敗。valuation API の失敗時・全銘柄空の時は Yahoo へ流さない
                   （失敗は空としてキャッシュし、同一実行内で再試行しない）
    """
    code5 = (code4 + "0") if len(code4) == 4 else code4
    if prices.get(code5) is None and prices.get(code4) is None:
        return None, None, None, 1.0, "skipped_non_tse"

    try:
        mcaps, _ = _fetch_valuation_mktcaps(api_key, price_date)
    except Exception as e:
        print(f"  !!! Valuation fetch failed: {type(e).__name__}: {e}")
        _VALUATION_CACHE[price_date] = ({}, price_date)
        return None, None, None, 1.0, None
    if not mcaps:
        return None, None, None, 1.0, None

    mcap = mcaps.get(code5)
    if mcap is None:
        mcap = mcaps.get(code4)
    if mcap is not None:
        return mcap, None, None, 1.0, "jquants"

    # 東証銘柄だが MktCap 無し → Yahoo フォールバック（遅延インポート）
    try:
        from market_cap_yahoo import fetch_market_cap_yahoo
        yahoo_value = fetch_market_cap_yahoo(code4)
    except Exception:
        yahoo_value = None
    if yahoo_value is not None:
        return yahoo_value, None, None, 1.0, "yahoo"

    return None, None, None, 1.0, None


def fetch_market_caps(codes: set[str], target_date: date) -> dict[str, float]:
    """証券コードのセットを受け取り、{code: 時価総額(億円)} の辞書を返す。"""
    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        print("  ERROR: JQUANTS_API_KEY not set. Returning empty (cache fallback will run).")
        return {}

    print(f"  Fetching market caps for {len(codes)} codes from J-Quants V2 (valuation)...")
    try:
        prices, price_date = _fetch_close_prices(api_key, target_date)
    except Exception as e:
        print(f"  !!! Close prices fetch failed: {type(e).__name__}: {e}")
        prices, price_date = {}, target_date
    print(f"    Close prices: {len(prices)} codes (date={price_date})")
    try:
        mcaps, mcap_date = _fetch_valuation_mktcaps(api_key, price_date)
        print(f"    Valuation MktCap: {len(mcaps)} codes (date={mcap_date})")
        if not mcaps:
            print("  !!! Valuation returned no MktCap. Returning empty.")
            return {}
    except Exception as e:
        # 銘柄ごとの再試行で同じ失敗を繰り返さないよう、ここで打ち切る
        print(f"  !!! Valuation fetch failed: {type(e).__name__}: {e}. Returning empty.")
        return {}

    market_caps: dict[str, float] = {}
    failed: list[tuple[str, str]] = []
    sources: Counter = Counter()
    for code4 in sorted(codes):
        mcap, _sh, _pe, _corr, source = compute_one(api_key, code4, prices, price_date)
        if mcap is not None:
            market_caps[code4] = mcap
            sources[source] += 1
        else:
            failed.append((code4, source or "unknown"))

    print(f"  Market caps resolved: {len(market_caps)} / {len(codes)} codes ({dict(sources)})")
    skipped = [f for f in failed if f[1] == "skipped_non_tse"]
    real_failed = [f for f in failed if f[1] != "skipped_non_tse"]
    if skipped:
        print(f"  Skipped (non-TSE): {len(skipped)} codes (e.g. {[c for c, _ in skipped[:5]]})")
    if real_failed:
        reasons = Counter(r for _, r in real_failed)
        print(f"  Failed: {len(real_failed)}. Reasons: {dict(reasons.most_common(5))}")

    return market_caps
