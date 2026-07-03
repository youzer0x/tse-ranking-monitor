#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市場分析（セクター/テーマ別 騰落率）の決定的データを J-Quants から生成する。

test-jquants の `script/sector_analysis.py` を本リポジトリへ移植した**無人実行版**。
既存の `jquants.py`（urllib クライアント）・`business_day.py`・`tdnet.py` を再利用し、
httpx / python-dotenv / matplotlib への依存を持ち込まない（配信リポの方針＝Stage1 は
requirements.txt、それ以外は stdlib に整合）。認証は環境変数 `JQUANTS_API_KEY`。

出力（--out-dir、既定 docs/tmp/market）:
  - sector_return_<date>.csv   : 33業種別の騰落率集計（sector_analysis.py とバイト互換）
  - movers_top_<date>.csv      : 値上がり/値下がり 上位30（同上）
  - market_stats_<date>.json   : CSV 外の決定的数値（TOPIX 前日比・breadth・最大代金銘柄 等）と
                                 執筆ヒント（⚠乖離フラグ候補・movers の TDnet 開示文脈）

後段の `build_market_json.py` が sector_return / movers_top CSV を読み、
market_stats JSON を `--stats` で受け取って `<date>_market.json` を組み立てる。

対象銘柄・騰落率の定義は sector_analysis.py と同一:
  - ProdCat=="011"（内国株券）・Mkt∈{0111,0112,0113}（プライム/スタンダード/グロース）
  - 5桁コード末尾 "0"（普通株）・当日売買代金 Va >= min_turnover（既定 1億円）
  - 騰落率 chg_pct = (当日 AdjC - 前営業日 AdjC) / 前営業日 AdjC * 100（分割・併合をクリーンに）
    表示用「当日終値」は調整前 C。

使い方:
  python scripts/build_market_stats.py --date 2026-07-02 --out-dir docs/tmp/market
  python scripts/build_market_stats.py --date 2026-07-02 --prev 2026-07-01 --out-dir docs/tmp/market
"""
import argparse
import csv
import json
import os
import statistics
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jquants
import tdnet

# Windows コンソール(cp932)対策: 標準出力を UTF-8 化。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# ── 定数 ──────────────────────────────────────────────────
PROBE_CODE = "72030"                      # 取引日特定用の参照銘柄（トヨタ自動車）
WINDOW_DAYS = 12                          # 取引日を遡って探す暦日数
TARGET_MKT = {"0111", "0112", "0113"}     # プライム / スタンダード / グロース
TARGET_PROD = "011"                       # 内国株券
MIN_TURNOVER = 100_000_000                # 流動性フィルタ既定（当日売買代金 1億円）
TOP_N_MOVERS = 30                         # movers CSV に載せる上位件数（値上/値下 各）
JST = timezone(timedelta(hours=9))

# ⚠乖離フラグ（大型株1銘柄がセクター騰落を歪めた候補）の検出閾値
FLAG_SIGN_MIN = 0.3     # sign(加重)≠sign(中央値) を採る最小 |加重|（pt）
FLAG_SPREAD_MIN = 2.0   # |加重 - 中央値| がこの pt 以上で候補
FLAG_SHARE_MIN = 0.5    # 支配銘柄の代金シェアがこの比率以上
FLAG_EXDOM_MIN = 1.0    # 支配銘柄を除いた加重との差がこの pt 以上


def die(msg):
    sys.stderr.write("[build_market_stats] ERROR: " + msg + "\n")
    raise SystemExit(1)


def nfkc(s):
    return unicodedata.normalize("NFKC", (s or "").strip())


# ── 取引日の解決（sector_analysis.py と同じ probe 方式でバイト互換の prev を保証）──
def resolve_prev_day(target_iso):
    """参照銘柄の四本値から対象日 T の直前営業日 T-1 を特定する。"""
    t = date.fromisoformat(target_iso)
    frm = (t - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = jquants.get("/equities/bars/daily",
                       {"code": PROBE_CODE, "from": frm, "to": target_iso})
    days = sorted({r["Date"] for r in rows if r.get("C") is not None})
    days = [d for d in days if d <= target_iso]
    if not days or days[-1] != target_iso:
        die("指定日 %s の確定データが見つからない（休場/未確定の可能性）" % target_iso)
    if len(days) < 2:
        die("営業日が2日分そろわない（取得 %d 日）" % len(days))
    return days[-2]


def fetch_topix(prev_day, target_day):
    """TOPIX の前日比%（終値ベース）と両日終値を返す。取得不可は (None, None, None)。"""
    try:
        rows = jquants.get("/indices/bars/daily/topix",
                           {"from": prev_day, "to": target_day})
    except Exception:
        return None, None, None
    series = {r["Date"]: r.get("C") for r in rows if r.get("C") is not None}
    ct, cp = series.get(target_day), series.get(prev_day)
    if ct is None or cp in (None, 0):
        return None, ct, cp
    return (ct - cp) / cp * 100.0, ct, cp


def fetch_disclosed_codes(target_day):
    """当日 fins/summary で決算開示のあった 5桁コード集合。取得不可は空集合。"""
    try:
        rows = jquants.get("/fins/summary", {"date": target_day})
    except Exception as e:
        sys.stderr.write("[build_market_stats] WARN fins/summary 取得不可: %s\n" % e)
        return set()
    return {r.get("Code", "") for r in rows}


# ── 集計 ──────────────────────────────────────────────────
def is_target(m):
    """対象（東証個別の普通株）か。jquants.is_tse_individual に末尾0条件を足す。"""
    return jquants.is_tse_individual(m) and (m.get("Code", "") or "").endswith("0")


def build_records(today_bars, prev_bars, master, min_turnover):
    """対象銘柄ごとに騰落率レコードを作り、流動性通過分と内訳統計を返す。"""
    records = []
    n_target = n_priced = 0
    for code5, mrow in master.items():
        if not is_target(mrow):
            continue
        n_target += 1
        trow = today_bars.get(code5)
        prow = prev_bars.get(code5)
        if trow is None or prow is None:
            continue
        adjc_t = trow.get("AdjC")
        adjc_p = prow.get("AdjC")
        if adjc_t is None or adjc_p in (None, 0):
            continue
        n_priced += 1
        records.append({
            "code5": code5,
            "code4": code5[:4],
            "name": mrow.get("CoName", ""),
            "market": mrow.get("MktNm", ""),
            "scale": mrow.get("ScaleCat", ""),
            "sector33": mrow.get("S33Nm", "") or "（未分類）",
            "close": trow.get("C"),
            "chg_pct": (adjc_t - adjc_p) / adjc_p * 100.0,
            "turnover": trow.get("Va"),
        })
    liquid = [r for r in records
              if r["turnover"] is not None and r["turnover"] >= min_turnover]
    stats = {
        "n_target": n_target,
        "n_priced": n_priced,
        "n_liquid": len(liquid),
        "min_turnover_yen": int(min_turnover),
    }
    return liquid, stats


def _breadth(records):
    up = sum(1 for r in records if r["chg_pct"] > 0)
    down = sum(1 for r in records if r["chg_pct"] < 0)
    flat = sum(1 for r in records if r["chg_pct"] == 0)
    return up, down, flat


def aggregate_by_sector(records):
    """33業種別に集計し、売買代金加重平均の降順で返す（sector_analysis.py と同一）。"""
    groups = {}
    for r in records:
        groups.setdefault(r["sector33"], []).append(r)
    out = []
    for sector, rows in groups.items():
        chgs = [r["chg_pct"] for r in rows]
        up, down, flat = _breadth(rows)
        total_va = sum(r["turnover"] for r in rows)
        w_mean = (sum(r["chg_pct"] * r["turnover"] for r in rows) / total_va
                  if total_va > 0 else None)
        out.append({
            "sector": sector, "n": len(rows), "up": up, "down": down, "flat": flat,
            "mean": statistics.fmean(chgs), "median": statistics.median(chgs),
            "w_mean": w_mean, "total_va": total_va,
        })
    out.sort(key=lambda x: (x["w_mean"] is None, -(x["w_mean"] or 0.0)))
    return out


def detect_divergence_flags(records, agg33):
    """⚠候補（大型株1銘柄によるセクター騰落の見かけ上の歪み）を機械検出する。"""
    groups = {}
    for r in records:
        groups.setdefault(r["sector33"], []).append(r)
    flags = []
    for a in agg33:
        rows = groups.get(a["sector"], [])
        w, med = a["w_mean"], a["median"]
        if not rows or w is None or med is None:
            continue
        dominant = max(rows, key=lambda r: (r["turnover"] or 0))
        total_va = sum((r["turnover"] or 0) for r in rows)
        dom_va = dominant["turnover"] or 0
        share = dom_va / total_va if total_va > 0 else 0.0
        va_ex = total_va - dom_va
        w_ex = (sum(r["chg_pct"] * (r["turnover"] or 0)
                    for r in rows if r is not dominant) / va_ex) if va_ex > 0 else None
        reasons = []
        if (w > 0) != (med > 0) and abs(w) >= FLAG_SIGN_MIN:
            reasons.append("sign_divergence")
        if abs(w - med) >= FLAG_SPREAD_MIN:
            reasons.append("weighted_median_spread")
        if share >= FLAG_SHARE_MIN and w_ex is not None and abs(w - w_ex) >= FLAG_EXDOM_MIN:
            reasons.append("dominant_stock")
        if not reasons:
            continue
        flags.append({
            "sector": a["sector"],
            "w_pct": round(w, 2), "median_pct": round(med, 2),
            "n": a["n"], "up": a["up"], "down": a["down"],
            "dominant": {
                "code": dominant["code4"], "name": nfkc(dominant["name"]),
                "pct": round(dominant["chg_pct"], 2),
                "turnover_oku": round(dom_va / 1e8, 1),
                "share_pct": round(share * 100, 1),
            },
            "w_pct_ex_dominant": round(w_ex, 2) if w_ex is not None else None,
            "reasons": reasons,
        })
    return flags


# ── CSV 出力（sector_analysis.py とバイト互換：utf-8-sig・CRLF・同書式）──
def _fmt(value, spec=""):
    if value is None:
        return "-"
    return format(value, spec) if spec else str(value)


def _oku(value):
    """円 → 億円（小数1桁・カンマ区切り）。"""
    return "-" if value is None else f"{value / 1e8:,.1f}"


def write_sector_csv(agg, out_dir, target_day):
    path = os.path.join(out_dir, "sector_return_%s.csv" % target_day)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["33業種", "銘柄数", "値上がり", "値下がり", "変わらず",
                    "売買代金加重騰落率%", "単純平均騰落率%", "中央値騰落率%", "売買代金合計(億円)"])
        for a in agg:
            w.writerow([
                a["sector"], a["n"], a["up"], a["down"], a["flat"],
                _fmt(a["w_mean"], "+.2f"), _fmt(a["mean"], "+.2f"),
                _fmt(a["median"], "+.2f"), _oku(a["total_va"]),
            ])
    return path


def write_movers_csv(records, disclosed, out_dir, target_day):
    gainers = sorted(records, key=lambda r: r["chg_pct"], reverse=True)[:TOP_N_MOVERS]
    losers = sorted(records, key=lambda r: r["chg_pct"])[:TOP_N_MOVERS]
    path = os.path.join(out_dir, "movers_top_%s.csv" % target_day)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["区分", "順位", "証券コード", "銘柄名", "上場区分", "規模区分",
                    "33業種", "当日終値", "前日比%", "売買代金(億円)", "当日決算開示"])
        for label, group in (("値上がり", gainers), ("値下がり", losers)):
            for i, r in enumerate(group, 1):
                w.writerow([
                    label, i, r["code4"], r["name"], r["market"], r["scale"],
                    r["sector33"], _fmt(r["close"], ",.1f"), _fmt(r["chg_pct"], "+.2f"),
                    _oku(r["turnover"]), "○" if r["code5"] in disclosed else "",
                ])
    return path


# ── market_stats JSON（CSV 外数値の決定的受け渡し）──
def build_stats_json(target_day, prev_day, generated_at, topix_pct, topix_close,
                     topix_prev_close, universe, liquid, agg33, flags, movers_ctx):
    up, down, flat = _breadth(liquid)
    top_sector = max(agg33, key=lambda a: (a["total_va"] or 0)) if agg33 else None
    top_stock = max(liquid, key=lambda r: (r["turnover"] or 0)) if liquid else None
    return {
        "schema_version": 1,
        "kind": "market_stats",
        "session_date": target_day,
        "prev_date": prev_day,
        "generated_at": generated_at,
        "topix_pct": round(topix_pct, 2) if topix_pct is not None else None,
        "topix_close": topix_close,
        "topix_prev_close": topix_prev_close,
        "universe": {
            "description": ("東証プライム/スタンダード/グロースの内国普通株のうち"
                            "当日売買代金%d億円以上" % (universe["min_turnover_yen"] // 100000000)),
            "min_turnover_yen": universe["min_turnover_yen"],
            "n_target": universe["n_target"],
            "n_priced": universe["n_priced"],
            "n_liquid": universe["n_liquid"],
        },
        "breadth": {"up": up, "down": down, "flat": flat},
        "top_sector_by_turnover": (
            {"name": top_sector["sector"], "turnover_oku": round(top_sector["total_va"] / 1e8, 1)}
            if top_sector else None),
        "top_stock_by_turnover": (
            {"code": top_stock["code4"], "name": nfkc(top_stock["name"]),
             "pct": round(top_stock["chg_pct"], 2),
             "turnover_oku": round((top_stock["turnover"] or 0) / 1e8, 1)}
            if top_stock else None),
        "strip_default": {
            "sectors_up": [a["sector"] for a in agg33[:3]],
            "sectors_down": [a["sector"] for a in agg33[-3:][::-1]],
        },
        "divergence_flags": flags,
        "movers_context": movers_ctx,
    }


def build_movers_context(liquid, prev_day, target_day):
    """movers 60銘柄（値上/値下 各30）について TDnet 開示文脈を best-effort で付す。"""
    gainers = sorted(liquid, key=lambda r: r["chg_pct"], reverse=True)[:TOP_N_MOVERS]
    losers = sorted(liquid, key=lambda r: r["chg_pct"])[:TOP_N_MOVERS]
    codes = {r["code4"] for r in gainers} | {r["code4"] for r in losers}
    try:
        win = tdnet.disclosures_window(prev_day, target_day)
    except Exception as e:
        sys.stderr.write("[build_market_stats] WARN TDnet 取得不可: %s\n" % e)
        return {}
    ctx = {}
    for c in sorted(codes):
        items = win.get(c)
        if items:
            ctx[c] = [{"date": d.get("date"), "time": d.get("time"), "title": d.get("title")}
                      for d in items]
    return ctx


def parse_args():
    p = argparse.ArgumentParser(description="市場分析の決定的データ（CSV＋stats JSON）を生成する")
    p.add_argument("--date", required=True, help="対象日 YYYY-MM-DD（東証営業日）")
    p.add_argument("--prev", default=None, help="前営業日 YYYY-MM-DD（省略時は probe で解決）")
    p.add_argument("--min-turnover", type=float, default=MIN_TURNOVER,
                   help="流動性フィルタの当日売買代金下限（円, 既定 %d）" % MIN_TURNOVER)
    p.add_argument("--out-dir", default="docs/tmp/market", help="出力先ディレクトリ")
    return p.parse_args()


def main():
    args = parse_args()
    try:
        target_day = date.fromisoformat(args.date).isoformat()
    except ValueError:
        die("--date は YYYY-MM-DD 形式で: %r" % args.date)

    prev_day = args.prev or resolve_prev_day(target_day)
    sys.stderr.write("[build_market_stats] T=%s / T-1=%s\n" % (target_day, prev_day))

    today_bars = jquants.bars_by_date(target_day)
    prev_bars = jquants.bars_by_date(prev_day)
    master = jquants.master_by_date(target_day)
    if not today_bars or not prev_bars or not master:
        die("bars/master が空（当日=%d 前日=%d マスタ=%d）"
            % (len(today_bars), len(prev_bars), len(master)))

    liquid, ustats = build_records(today_bars, prev_bars, master, args.min_turnover)
    if not liquid:
        die("流動性フィルタ通過銘柄が0件")
    agg33 = aggregate_by_sector(liquid)
    if len(agg33) != 33:
        die("集計セクター数が33でない（実際: %d）" % len(agg33))

    disclosed = fetch_disclosed_codes(target_day)
    topix_pct, topix_close, topix_prev = fetch_topix(prev_day, target_day)
    flags = detect_divergence_flags(liquid, agg33)
    movers_ctx = build_movers_context(liquid, prev_day, target_day)
    generated_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    os.makedirs(args.out_dir, exist_ok=True)
    p_sec = write_sector_csv(agg33, args.out_dir, target_day)
    p_mov = write_movers_csv(liquid, disclosed, args.out_dir, target_day)

    stats = build_stats_json(target_day, prev_day, generated_at, topix_pct,
                             topix_close, topix_prev, ustats, liquid, agg33, flags, movers_ctx)
    p_stats = os.path.join(args.out_dir, "market_stats_%s.json" % target_day)
    with open(p_stats, "w", encoding="utf-8", newline="\n") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
        f.write("\n")

    up, down, flat = stats["breadth"]["up"], stats["breadth"]["down"], stats["breadth"]["flat"]
    sys.stderr.write(
        "[build_market_stats] OK: %s / %s / %s（33業種 / breadth %d-%d-%d / n_liquid %d / "
        "TOPIX %s / flags %d）\n"
        % (p_sec, p_mov, p_stats, up, down, flat, ustats["n_liquid"],
           ("%+.2f%%" % topix_pct) if topix_pct is not None else "N/A", len(flags)))


if __name__ == "__main__":
    main()
