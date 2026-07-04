# vendored-from: market-scripts-common — このファイルは共有リポジトリの正本のコピーです。
# 消費リポジトリでは編集禁止。変更は market-scripts-common で行い sync.py で配布すること。
"""J-Quants V2 API クライアント（市場区分・終値・四本値・発行済株式数・時価総額）。

- 認証: ヘッダ `x-api-key`（環境変数 JQUANTS_API_KEY）。Light プラン以上で当日値が取れる。
- 東証個別株の権威判定: master の ProdCat=="011"（内国株券）かつ Mkt∈{0111,0112,0113}
  （プライム/スタンダード/グロース）。地方単独上場は bars/daily 非収録で自動除外。
- 東証日中ランキング（tse 系）では bars/daily の日通し終値 C・売買代金 Va・調整済み終値 AdjC を使う。
  値上がり率＝当日 AdjC ÷ 前営業日 AdjC（調整済みの連日比で分割・併合をクリーンに処理）。
  時価総額の算出は market_cap_jquants.py（tdnet-monitor 由来の算出方式）に委譲する。
- PTS ナイトランキング（pts 系）では時価総額を本モジュールで算出する:
  時価総額(億円) = 取引所終値 × 発行済株式数(ShOutFY) × 分割/併合補正(AdjFactor) / 1e8。
  AdjFactor は株式分割・併合のみ補正。増資・自己株消却は非対象 → 株探の最新株数と
  クロスチェックして乖離が大きい銘柄は呼び出し側で「†」注記する。

stdlib のみ（urllib）。
"""
import os, json, time, urllib.request, urllib.error, urllib.parse
from datetime import date, timedelta

API = "https://api.jquants.com/v2"

# データ鮮度プローブ用の参照銘柄と探索窓（build_market_stats.py の resolve_prev_day と同値）。
PROBE_CODE = "72030"   # トヨタ自動車（プライム大型・毎営業日必ず出来高がある基準銘柄）
WINDOW_DAYS = 12       # 四本値を遡って探す暦日数（連休を跨いでも直近営業日に届く）


def _key():
    k = os.environ.get("JQUANTS_API_KEY")
    if not k:
        raise SystemExit("JQUANTS_API_KEY not set")
    return k


def get(path, params, max_pages=80):
    """V2 を呼び pagination_key を連結して data 配列を返す（429 リトライ込み）。"""
    key = _key()
    out, pk = [], None
    for _ in range(max_pages):
        p = dict(params)
        if pk:
            p["pagination_key"] = pk
        url = f"{API}{path}?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(url, headers={"x-api-key": key})
        body = None
        for attempt in range(9):
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(min(1.5 * (attempt + 1), 12)); continue
                raise
            except Exception:
                if attempt == 8:
                    raise
                time.sleep(1.5 ** attempt)
        if body is None:
            raise RuntimeError(f"empty/429-exhausted: {path} {params}")
        out.extend(body.get("data", []))
        pk = body.get("pagination_key")
        if not pk:
            break
    return out


def code5(code4):
    """株探/TDnet の4桁（または英数）コードを J-Quants の5桁へ。9760→97600, 285A→285A0。"""
    return code4 + "0" if len(code4) == 4 else code4


def code4(code5):
    """J-Quants の5桁コードを4桁（または末尾英字）へ。97600→9760, 285A0→285A。"""
    c = str(code5)
    # 5桁コードは「4桁の証券コード＋予約桁0」。証券コードは数字4桁、または
    # 新方式の英数字4桁（数字3桁＋英字1桁、例 268A）。末尾0を一律で外す。
    if len(c) == 5 and c.endswith("0") and c[:4].isalnum():
        return c[:-1]
    return c


def normalize_company_name(name):
    """表示用に社名を整える。

    - J-Quants の CoName はフルネームで返る（例：村田製作所／ソフトバンクグループ）が、
      英数字・記号は**全角**のことがある（例：ＡＧＣ／ＫＤＤＩ／日本Ｍ＆Ａセンター…）。
    - 仕様：英数記号は**半角**に統一し、万一含まれる「株式会社」表記は除去する
      （CoName は通常含まないが安全側）。日本語（かな・カナ・漢字）はそのまま。
    """
    if not name:
        return name
    out = []
    for ch in name:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:      # 全角の英数字・記号 → 半角（! 〜 ~）
            out.append(chr(o - 0xFEE0))
        elif o == 0x3000:               # 全角スペース → 半角スペース
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    for token in ("株式会社", "（株）", "(株)", "㈱"):
        s = s.replace(token, "")
    return s.strip()


def master_by_date(target_iso):
    return {r["Code"]: r for r in get("/equities/master", {"date": target_iso})}


def bars_by_date(target_iso):
    return {r["Code"]: r for r in get("/equities/bars/daily", {"date": target_iso})}


def last_confirmed_session(target_iso, probe_code=PROBE_CODE, window_days=WINDOW_DAYS):
    """プローブ銘柄の四本値から、target_iso 以前で「当日終値 C が確定している」最新営業日を返す。

    - 当日 target_iso の四本値が確定していれば target_iso を返す（＝当日データ到達の判定に使う）。
    - まだ当日が未確定なら直近確定営業日（< target_iso）を返す。
    - window_days 内に1日も確定が無ければ None。

    build_market_stats.resolve_prev_day のプローブ核（days=sorted({Date | C is not None}) の最大）を
    共有関数化したもの。単一銘柄の bars/daily を範囲取得するだけの安価な呼び出し。
    """
    t = date.fromisoformat(target_iso)
    frm = (t - timedelta(days=window_days)).isoformat()
    rows = get("/equities/bars/daily", {"code": probe_code, "from": frm, "to": target_iso})
    days = sorted({r["Date"] for r in rows if r.get("C") is not None})
    days = [d for d in days if d <= target_iso]
    return days[-1] if days else None


def is_tse_individual(m):
    """master 行が東証本則の内国株券か。"""
    return bool(m) and m.get("ProdCat") == "011" and m.get("Mkt") in ("0111", "0112", "0113")


def latest_shares(code4):
    """最新の (期末日 CurFYEn, 期末発行済株式数 ShOutFY) を返す。無ければ None。"""
    data = get("/fins/summary", {"code": code4})
    cands = []
    for r in data:
        sh = r.get("ShOutFY"); disc = r.get("DiscDate"); cur = r.get("CurFYEn") or disc
        if sh in (None, "", 0) or not disc:
            continue
        cands.append((disc, cur, sh))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    _disc, cur, sh = cands[-1]
    try:
        return cur, int(float(sh))
    except (ValueError, TypeError):
        return None


def split_corr(code4, since_iso, target_iso):
    """since の翌日〜target の AdjFactor 累積積の逆数（分割・併合の株数補正係数）。"""
    try:
        since = date.fromisoformat(since_iso); tgt = date.fromisoformat(target_iso)
    except (ValueError, TypeError):
        return 1.0
    if since >= tgt:
        return 1.0
    data = get("/equities/bars/daily",
               {"code": code4, "from": (since + timedelta(days=1)).isoformat(), "to": target_iso})
    corr = 1.0
    for r in data:
        f = r.get("AdjFactor")
        if f and float(f) != 1.0:
            corr /= float(f)
    return corr


def market_cap_oku(code4, close, target_iso):
    """1銘柄の時価総額(億円)を算出して返す。返り値: (mcap_oku|None, shoutfy|None, cur_end|None, corr)。"""
    if close is None:
        return None, None, None, 1.0
    sh = latest_shares(code4)
    if not sh:
        return None, None, None, 1.0
    cur_end, shoutfy = sh
    try:
        corr = split_corr(code4, cur_end, target_iso)
    except Exception:
        corr = 1.0
    return close * shoutfy * corr / 1e8, shoutfy, cur_end, corr
