# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""時価総額データの取得 (J-Quants V2 API)

J-Quants V2 から終値・発行済株式数・分割係数を取得し、時価総額(億円)を算出する。
fins/summary に決算データが無い銘柄 (新規上場等) は market_cap_yahoo に委譲する。

算出方式（要点）:
  時価総額(億円) = AdjC(調整済み終値) × ShOutFY(期末発行済株式数) × 分割補正 / 1e8
  - 終値は AdjC を用い、当日に無ければ最大 LOOKBACK_DAYS 営業日さかのぼる。
  - ShOutFY は CurFYEn(期末日) を分割補正の起点にとる（DiscDate ではなく）。
  - 分割補正 = (period_end+1 〜 price_date) の AdjFactor 累積積の逆数。
  - fins/summary に決算が無い銘柄は Yahoo Finance JP にフォールバック。

正本は market-scripts-common（算出方式の出自は tdnet-monitor）。tdnet-monitor を含む各消費リポへは
sync.py で配布する。Yahoo インポートは遅延（フォールバック時のみ）。
"""

import os
import time
import requests
from datetime import date, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed


BASE = "https://api.jquants.com/v2"
TIMEOUT = 20
MAX_RETRY = 5
LOOKBACK_DAYS = 5
RATE_SLEEP = 0.25
MAX_WORKERS = 3

# (prices, price_date) を target_date ごとにキャッシュ。
_PRICES_CACHE: dict[date, tuple[dict[str, float], date]] = {}


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


def _normalize_code(code5: str) -> str:
    """J-Quants の 5 桁コードを TDnet 表記 (4 桁または末尾英字) に正規化"""
    if len(code5) == 5 and code5.endswith("0") and code5[:-1].isdigit():
        return code5[:-1]
    return code5


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


def _fetch_latest_shares(api_key: str, code4: str) -> tuple[date, int] | None:
    """銘柄ごとに最新の (period_end, ShOutFY) を返す。

    period_end は CurFYEn (期末日)。ShOutFY は「期末発行済株式数」なので、
    期末日を分割補正の起点とするのが正しい (DiscDate ではなく)。
    CurFYEn が欠落している場合は DiscDate にフォールバック。
    """
    rows = _request(api_key, "/fins/summary", {"code": code4})
    candidates: list[tuple[str, str, str]] = []
    for r in rows:
        sh = r.get("ShOutFY")
        disc = r.get("DiscDate")
        cur_end = r.get("CurFYEn") or disc
        if sh in (None, "", 0) or not disc:
            continue
        candidates.append((disc, cur_end, sh))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _disc, cur_end, sh = candidates[-1]
    try:
        return date.fromisoformat(cur_end), int(float(sh))
    except (ValueError, TypeError):
        return None


def _fetch_split_correction(api_key: str, code4: str, since: date, until: date) -> float:
    """since+1日 〜 until の AdjFactor 累積積の逆数を返す。
    1:2 分割なら AdjFactor=0.5 → 返り値 2.0 (株数を 2 倍する補正係数)。
    """
    if since >= until:
        return 1.0
    rows = _request(api_key, "/equities/bars/daily", {
        "code": code4,
        "from": (since + timedelta(days=1)).isoformat(),
        "to": until.isoformat(),
    })
    correction = 1.0
    for r in rows:
        f = r.get("AdjFactor")
        if f and float(f) != 1.0:
            correction /= float(f)
    return correction


def compute_one(api_key: str, code4: str, prices: dict[str, float], price_date: date):
    """1銘柄の時価総額(億円)と内訳を返す。

    返り値: (mcap_oku|None, shoutfy|None, period_end|None, corr, source)
      source ∈ {"jquants", "yahoo", "skipped_non_tse", None}
      - "jquants": ShOutFY×AdjC×corr で算出（† クロスチェック可）
      - "yahoo"  : 新規上場等で fins/summary 無し → Yahoo フォールバック（shoutfy/corr は None/1.0）
    """
    code5 = (code4 + "0") if len(code4) == 4 else code4
    close = prices.get(code5) or prices.get(code4)
    if close is None:
        return None, None, None, 1.0, "skipped_non_tse"

    try:
        sh = _fetch_latest_shares(api_key, code4)
    except Exception:
        sh = None

    if sh is not None:
        period_end, shoutfy = sh
        try:
            corr = _fetch_split_correction(api_key, code4, period_end, price_date)
        except Exception:
            corr = 1.0
        mcap_oku = close * shoutfy * corr / 1e8
        return round(mcap_oku, 1), shoutfy, period_end, corr, "jquants"

    # 東証銘柄だが ShOutFY 取得失敗 → Yahoo フォールバック（遅延インポート）
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

    print(f"  Fetching market caps for {len(codes)} codes from J-Quants V2...")
    try:
        prices, price_date = _fetch_close_prices(api_key, target_date)
    except Exception as e:
        print(f"  !!! Close prices fetch failed: {type(e).__name__}: {e}")
        prices, price_date = {}, target_date
    print(f"    Close prices: {len(prices)} codes (date={price_date})")

    market_caps: dict[str, float] = {}
    failed: list[tuple[str, str]] = []

    def worker(code4: str):
        mcap, _sh, _pe, _corr, source = compute_one(api_key, code4, prices, price_date)
        return code4, mcap, (None if mcap is not None else (source or "unknown"))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {}
        for c in sorted(codes):
            futures[ex.submit(worker, c)] = c
            time.sleep(RATE_SLEEP)
        done = 0
        for fut in as_completed(futures):
            code, mcap, reason = fut.result()
            done += 1
            if mcap is not None:
                market_caps[code] = mcap
            else:
                failed.append((code, reason or "unknown"))
            if done % 50 == 0:
                print(f"    ... {done}/{len(codes)} processed")

    print(f"  Market caps resolved: {len(market_caps)} / {len(codes)} codes")
    skipped = [f for f in failed if f[1] == "skipped_non_tse"]
    real_failed = [f for f in failed if f[1] != "skipped_non_tse"]
    if skipped:
        print(f"  Skipped (non-TSE): {len(skipped)} codes (e.g. {[c for c, _ in skipped[:5]]})")
    if real_failed:
        reasons = Counter(r for _, r in real_failed)
        print(f"  Failed: {len(real_failed)}. Reasons: {dict(reasons.most_common(5))}")

    return market_caps
