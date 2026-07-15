"""Stage 1（決定的）: 東証 日中（レギュラー）値上がり率ランキングの素データを組み立てて JSON 出力する。

  J-Quants V2（全銘柄の当日 bars/daily）だけでスクリーニングが完結する：
    - 値上がり率 ＝ 当日 AdjC ÷ 前営業日 AdjC − 1（調整済み終値の連日比＝分割/併合クリーン）
    - 売買代金 ＝ Va（日通し取引代金・実値）
    - 時価総額 ＝ market_cap_jquants（AdjC×ShOutFY×分割補正/1e8、新規上場は Yahoo）
  TDnet（前回引け→当日引け直前の開示）と株探（† 最新株数）を結合する。
  **変動要因（[開示]/[報道]/[テーマ]）は含めない**（後段で Claude が裏取りして埋める）。

フィルタ（既定）:
  - 東証個別株のみ（J-Quants ProdCat=011 かつ Mkt∈{0111,0112,0113}）。ETF/REIT/地方上場は除外。
  - 値上がり率 ≥ min_pct（既定 +5%）かつ 売買代金 ≥ min_turnover（既定 ¥10,000,000）。
  - 時価総額 ≥ min_mcap 億円（既定 100）。
  - 期中の増資・自己株で J-Quants 株数と株探最新株数が >1% 乖離する銘柄は mcap_flag="†"。
  - 上記を満たす銘柄が max_rank（既定 30）社を超える場合は、値上がり率の高い順に上位 max_rank 社のみをランキング対象とする
    （--max-rank 0 で上限なし）。該当総数は counts.qualifying、掲載数は counts.ranked に記録する。

usage:
  python build_day_ranking.py [--date YYYY-MM-DD] [--prev YYYY-MM-DD] [--out ranking.json]
  （--date 省略時は当日が営業日ならその日、--prev 省略時は直近営業日）
"""
import sys, os, json, time, argparse, datetime
from datetime import date, timedelta, timezone
from pathlib import Path

# 共有ベンダーは scripts/ 配置を維持する。パッケージを直接 import した場合も解決できるようにする。
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import jquants, kabutan_pts, tdnet, business_day, market_cap_jquants as mcap

from .contracts import validate_ranking_document as _validate_ranking_contract

JST = timezone(timedelta(hours=9))
MIN_BARS_PREV_RATIO = 0.90
MIN_MASTER_BARS_COVERAGE = 0.90


def _validate_rows_date(rows, expected_iso, label):
    """J-Quants辞書のキー・Code・Dateが要求日と矛盾しないことを確認する。"""
    for key, row in rows.items():
        code = row.get("Code")
        if code is not None and str(code) != str(key):
            raise ValueError(f"{label}: key {key!r} != row.Code {code!r}")
        row_date = row.get("Date")
        if row_date is not None and row_date != expected_iso:
            raise ValueError(f"{label}: requested {expected_iso}, got row.Date={row_date} ({key})")


def validate_source_data(session_iso, prev_iso, prev5_iso, master, bars_now, bars_prev, bars_prev5,
                         min_bars_ratio=MIN_BARS_PREV_RATIO,
                         min_master_coverage=MIN_MASTER_BARS_COVERAGE):
    """Stage1単独実行でも部分データを公開しないための入力検証。

    ゲートを迂回して呼ばれても、当日barsの前日比と東証個別株masterに対するbars収録率を
    二重に確認する。閾値未満・空データ・日付矛盾は明示的に拒否する。
    """
    session_d = date.fromisoformat(session_iso)
    prev_d = date.fromisoformat(prev_iso)
    prev5_d = date.fromisoformat(prev5_iso)
    if not prev5_d < prev_d < session_d:
        raise ValueError(
            f"invalid date order: prev5={prev5_iso}, prev={prev_iso}, session={session_iso}")
    expected_prev = business_day.prev_business_day(session_d).isoformat()
    if prev_iso != expected_prev:
        raise ValueError(f"prev_date mismatch: expected {expected_prev}, got {prev_iso}")
    expected_prev5 = business_day.nth_prev_business_day(session_d, 5).isoformat()
    if prev5_iso != expected_prev5:
        raise ValueError(f"prev5_date mismatch: expected {expected_prev5}, got {prev5_iso}")

    if not master:
        raise ValueError("incomplete source data: session master is empty")
    if not bars_prev:
        raise ValueError("incomplete source data: previous-session bars are empty")
    if not bars_now:
        raise ValueError("incomplete source data: session bars are empty")

    _validate_rows_date(master, session_iso, "master")
    _validate_rows_date(bars_now, session_iso, "session bars")
    _validate_rows_date(bars_prev, prev_iso, "previous bars")
    _validate_rows_date(bars_prev5, prev5_iso, "five-session-prior bars")

    bars_ratio = len(bars_now) / len(bars_prev)
    if bars_ratio < min_bars_ratio:
        raise ValueError(
            "incomplete source data: session/previous bars ratio "
            f"{bars_ratio:.3f} < {min_bars_ratio:.3f} "
            f"({len(bars_now)}/{len(bars_prev)})")

    eligible_codes = {code for code, row in master.items() if jquants.is_tse_individual(row)}
    if not eligible_codes:
        raise ValueError("incomplete source data: master has no TSE individual stocks")
    covered = len(eligible_codes.intersection(bars_now))
    master_coverage = covered / len(eligible_codes)
    if master_coverage < min_master_coverage:
        raise ValueError(
            "incomplete source data: master/bars coverage "
            f"{master_coverage:.3f} < {min_master_coverage:.3f} "
            f"({covered}/{len(eligible_codes)})")
    return {"bars_prev_ratio": bars_ratio, "master_bars_coverage": master_coverage}


def validate_ranking_document(data):
    """Stage1出力のrank/counts契約を公開前に検証し、同じdictを返す。"""
    return _validate_ranking_contract(data, require_stage1_counts=True)


def annotate_sector_clusters(rows, min_cluster=2):
    """同一 S33（33業種）で当日ともに上昇した銘柄群を機械的に束ね、各行に
    `sector_cluster`（同業種 co-mover ＋ leader 候補）を付す。leader は
    クラスタ内で具体的 TDnet 開示を持つ銘柄を優先（複数なら売買代金最大）、
    開示が無ければ売買代金最大とし、`leader_basis`（"disclosure"/"turnover"）で
    どちらかを明示する。決定的・ネットワーク呼び出し無し（rows は rank/disclosures
    付与後に渡すこと）。

    狙い：「孤立した謎の上昇」ではなく同業種が束で動いた銘柄を surface し、後段の
    変動要因リサーチ（手順B 2.5）が leader への連鎖として帰属できるようにする。
    注意：S33 は**同業種内のみ**を束ねる。業種をまたぐテーマ（例：光部品＝非鉄金属
    〔電線〕＋電気機器〔光部品〕＋精密機器）は本関数では結びつけない。横断的な
    テーマ結合は方法論側（手順B 2.5・theme_clusters の各 leader と地合い）で人手で行う。

    戻り値：size>=min_cluster のクラスタ要約リスト（theme_clusters）。
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        if r.get("sec33"):
            groups[r["sec33"]].append(r)
    summary = []
    for s33, members in groups.items():
        if len(members) < min_cluster:
            continue
        disc = [m for m in members if m.get("disclosures")]
        leader = (max(disc, key=lambda m: m["turnover_yen"]) if disc
                  else max(members, key=lambda m: m["turnover_yen"]))
        basis = "disclosure" if disc else "turnover"
        sec_name = members[0].get("sec33_name")
        for r in members:
            r["sector_cluster"] = {
                "sec33": s33, "name": r.get("sec33_name"), "size": len(members),
                "peers": [{"code": m["code"], "name": m["name"], "rank": m.get("rank"),
                           "pct": m["pct"], "turnover_m": m["turnover_m"],
                           "has_disclosure": bool(m.get("disclosures"))}
                          for m in members if m["code"] != r["code"]],
                "leader_code": leader["code"], "leader_basis": basis,
            }
        summary.append({
            "sec33": s33, "name": sec_name, "size": len(members),
            "members": [m["code"] for m in members],
            "leader_code": leader["code"], "leader_basis": basis,
        })
    summary.sort(key=lambda c: -c["size"])
    return summary


def build(session_iso, prev_iso, min_pct=5.0, min_turnover=10_000_000, min_mcap=100,
          max_rank=30, do_kabutan_shares=True, do_kabutan_news=False,
          kabutan_news_top=30, verbose=True):
    def log(*a):
        if verbose:
            print(*a, file=sys.stderr)

    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        raise SystemExit("JQUANTS_API_KEY not set")
    session_d = date.fromisoformat(session_iso)
    prev_d = date.fromisoformat(prev_iso)

    # 1) J-Quants 一括（master / bars 当日・前営業日・5営業日前）
    prev5_iso = business_day.nth_prev_business_day(session_d, 5).isoformat()
    log(f"# J-Quants master/bars: session={session_iso} prev={prev_iso} prev5={prev5_iso} ...")
    master = jquants.master_by_date(session_iso)
    bars_now = jquants.bars_by_date(session_iso)
    bars_prev = jquants.bars_by_date(prev_iso)
    bars_prev5 = jquants.bars_by_date(prev5_iso)
    log(f"# master={len(master)} bars_now={len(bars_now)} bars_prev={len(bars_prev)} bars_prev5={len(bars_prev5)}")
    source_quality = validate_source_data(
        session_iso, prev_iso, prev5_iso, master, bars_now, bars_prev, bars_prev5)
    log("# source quality: bars/prev=%.1f%% master coverage=%.1f%%"
        % (source_quality["bars_prev_ratio"] * 100,
           source_quality["master_bars_coverage"] * 100))

    # 当日 AdjC を時価総額モジュールのキャッシュに流し込む（bars/daily の再取得を避ける）
    prices = {c: b["AdjC"] for c, b in bars_now.items() if b.get("AdjC") is not None}
    mcap.prime_price_cache(session_d, prices)

    # 2) スクリーニング（J-Quants のみ・全銘柄ループ）
    cand, excluded = [], []
    for c5, b in bars_now.items():
        m = master.get(c5)
        if not jquants.is_tse_individual(m):
            excluded.append({"code": jquants.code4(c5), "reason": "not_tse_individual"})
            continue
        adjC = b.get("AdjC")
        bp = bars_prev.get(c5)
        adjCp = bp.get("AdjC") if bp else None
        if not adjC or not adjCp:
            excluded.append({"code": jquants.code4(c5), "reason": "no_prev_close"})
            continue
        pct = (adjC / adjCp - 1.0) * 100.0
        if pct < min_pct:
            continue
        # 直近5営業日騰落率（5営業日前比）＝当日 AdjC ÷ 5営業日前 AdjC − 1。5営業日前値が無ければ None。
        b5 = bars_prev5.get(c5)
        adjC5 = b5.get("AdjC") if b5 else None
        pct5 = round((adjC / adjC5 - 1.0) * 100.0, 2) if (adjC and adjC5) else None
        cand.append((c5, b, m, pct, pct5))
    # 値上がり率の高い順。同率は売買代金の大きい順（上限 max_rank の境界を決定的にする）
    cand.sort(key=lambda x: (-x[3], -(x[1].get("Va") or 0)))
    log(f"# candidates(>= +{min_pct}%, TSE individual, prev-close 有)={len(cand)}")

    # 3) 売買代金 → 時価総額 の順でフィルタ（候補のみ・少数なので per-code 呼び出しで十分）
    qualifying, dropped_turnover, dropped_mcap = [], [], []
    for c5, b, m, pct, pct5 in cand:
        c4 = jquants.code4(c5)
        name = jquants.normalize_company_name(m.get("CoName"))
        va = b.get("Va")
        if va is None or va < min_turnover:
            dropped_turnover.append({"code": c4, "name": name, "pct": round(pct, 2), "pct5": pct5,
                                     "turnover_m": (round(va / 1e6, 1) if va else 0.0)})
            continue
        mc, shoutfy, period_end, corr, source = mcap.compute_one(api_key, c4, prices, session_d)
        time.sleep(0.1)
        if mc is None or mc < min_mcap:
            dropped_mcap.append({"code": c4, "name": name, "pct": round(pct, 2), "pct5": pct5,
                                 "turnover_m": round(va / 1e6, 1),
                                 "mcap_oku": (round(mc) if mc is not None else None),
                                 "mcap_source": source})
            continue
        qualifying.append(dict(
            code=c4, name=name, market=m.get("MktNm"),
            sec17=m.get("S17"), sec17_name=m.get("S17Nm"),
            sec33=m.get("S33"), sec33_name=m.get("S33Nm"),
            scale_cat=m.get("ScaleCat"),
            mcap_oku=round(mc), mcap_oku_exact=mc, mcap_flag="", mcap_source=source,
            pct=round(pct, 2), pct5=pct5, close=b.get("C"), adj_close=b.get("AdjC"),
            prev_adj_close=(bars_prev.get(c5) or {}).get("AdjC"),
            turnover_yen=round(va), turnover_m=round(va / 1e6, 1),
            shoutfy_jq=shoutfy, period_end=(period_end.isoformat() if period_end else None),
            corr=round(corr, 6), disclosures=[], kabutan_news=[],
            factor="", factor_kind=""))

    # 上限：該当総数が max_rank を超えたら値上がり率上位 max_rank 社のみをランキング対象にする。
    # qualifying は cand のソート順（pct 降順）を継承しているので先頭から切り出せばよい。
    qualified_total = len(qualifying)
    capped = bool(max_rank and max_rank > 0 and qualified_total > max_rank)
    if capped:
        qualifying = qualifying[:max_rank]
    for i, row in enumerate(qualifying, 1):
        row["rank"] = i
    log(f"# qualifying(該当)={qualified_total}  ranked(掲載)={len(qualifying)}"
        f"{f' [上限{max_rank}社]' if capped else ''}  dropped_turnover={len(dropped_turnover)}  "
        f"dropped_mcap={len(dropped_mcap)}  excluded={len(excluded)}")

    # 4) TDnet 変動要因候補（前営業日>=15:30 ∪ 当日<15:30 の厳密窓・自前取得）
    log(f"# TDnet window: {prev_iso}>=15:30 ∪ {session_iso}<15:30 ...")
    try:
        by = tdnet.disclosures_window(prev_d, session_d)
    except Exception as e:
        log(f"# WARN tdnet: {type(e).__name__}: {e}")
        by = {}
    for row in qualifying:
        row["disclosures"] = by.get(row["code"], [])

    # 4.5) セクター連動クラスタ注釈（決定的・同一 S33 で束ね leader を特定）。
    #      開示有無を leader 判定に使うため TDnet 付与の直後に実行する。
    theme_clusters = annotate_sector_clusters(qualifying)
    log(f"# sector clusters(size>=2)={len(theme_clusters)}")

    # 5) 株探 最新発行済株式数とのクロスチェック（† 注記。source=='jquants' のみ）
    if do_kabutan_shares:
        log(f"# kabutan shares cross-check for {len(qualifying)} names ...")
        for row in qualifying:
            if row["mcap_source"] != "jquants" or not row["shoutfy_jq"]:
                continue
            shk = kabutan_pts.kabutan_shares(row["code"])
            time.sleep(0.2)
            base = (row["shoutfy_jq"] or 0) * (row["corr"] or 1.0)
            if shk and base > 0 and abs(shk - base) / base > 0.01:
                row["mcap_flag"] = "†"
                row["shares_kabutan"] = shk
                if row["close"]:
                    row["mcap_kabutan_oku"] = round(row["close"] * shk / 1e8)

    # 6) 株探 銘柄ニュース（材料・特集〔レーティング日報〕・5%ルール等）の事前充填（任意）。
    #    変動要因リサーチが「材料未確認」へ落とす前に必ず材料/レーティング見出しを確認できる。
    #    best-effort（失敗は [] でスキップ）。エンリッチであって権威ではない（Claude が裏取り）。
    if do_kabutan_news:
        top = qualifying[:kabutan_news_top] if kabutan_news_top else qualifying
        log(f"# kabutan news prefetch for {len(top)} names ...")
        for row in top:
            row["kabutan_news"] = kabutan_pts.kabutan_news(row["code"])
            time.sleep(0.3)

    result = {
        "schema_version": 1,
        "session_date": session_iso,
        "prev_date": prev_iso,
        "prev5_date": prev5_iso,
        "session_window": f"{session_iso} 09:00–15:30 JST（前場9:00-11:30／後場12:30-15:30）",
        "generated_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "criteria": {"min_pct": min_pct, "min_turnover_yen": min_turnover, "min_mcap_oku": min_mcap,
                     "max_rank": (max_rank if (max_rank and max_rank > 0) else None)},
        "capped": capped,
        "counts": {"qualifying": qualified_total, "ranked": len(qualifying),
                   "dropped_turnover": len(dropped_turnover),
                   "dropped_mcap": len(dropped_mcap), "excluded": len(excluded)},
        "rows": qualifying,
        "theme_clusters": theme_clusters,
        "dropped_turnover": sorted(dropped_turnover, key=lambda x: -x["pct"]),
        "dropped_mcap": sorted(dropped_mcap, key=lambda x: -x["pct"]),
    }
    return validate_ranking_document(result)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="セッション日 YYYY-MM-DD（省略時は当日が営業日ならその日）")
    ap.add_argument("--prev", help="前営業日 YYYY-MM-DD（省略時は直近営業日）")
    ap.add_argument("--min-pct", type=float, default=5.0)
    ap.add_argument("--min-turnover", type=float, default=10_000_000)
    ap.add_argument("--min-mcap", type=float, default=100)
    ap.add_argument("--max-rank", type=int, default=30,
                    help="掲載上限（値上がり率上位N社。0で上限なし。既定30）")
    ap.add_argument("--out", help="JSON 出力先パス（省略時は stdout）")
    ap.add_argument("--no-kabutan-shares", action="store_true")
    ap.add_argument("--kabutan-news", action="store_true",
                    help="株探 銘柄ニュース（材料/特集〔レーティング日報〕/5%%ルール）の見出しを各行に事前充填")
    ap.add_argument("--kabutan-news-top", type=int, default=30,
                    help="kabutan-news を充填する上位N社（0で全掲載行。既定30）")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    if args.date:
        session_iso = args.date
    else:
        sd = business_day.tse_session_date_for(datetime.datetime.now(JST).date())
        if sd is None:
            print("# 本日は東証休場（新規セッション無し）。スキップ。", file=sys.stderr)
            return
        session_iso = sd.isoformat()
    prev_iso = args.prev or business_day.prev_business_day(date.fromisoformat(session_iso)).isoformat()

    data = build(session_iso, prev_iso, min_pct=args.min_pct, min_turnover=args.min_turnover,
                 min_mcap=args.min_mcap, max_rank=args.max_rank,
                 do_kabutan_shares=not args.no_kabutan_shares,
                 do_kabutan_news=args.kabutan_news, kabutan_news_top=args.kabutan_news_top)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        d = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(d, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        c = data["counts"]
        print(f"# wrote {args.out} (該当{c['qualifying']}社 / 掲載{c['ranked']}社"
              f"{' [上限]' if data.get('capped') else ''})", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
