#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市場分析（セクター/テーマ 騰落率分析）の Web 配信用 JSON を組み立てる。

test-jquants の決定的スクリプト `sector_analysis.py` が出力した CSV
（sector_return_<date>.csv / movers_top_<date>.csv）から**数値を機械転記**し、
Claude/人が執筆した**ナラティブ・フラグメント JSON**（テーゼ・背景・材料・出典）と
結合して `docs/data/<date>_market.json`（スキーマ v1）を生成する。

数値とナラティブを分離することで、将来の日次自動化（決定的スクリプト → Claude が
フラグメント執筆 → 本スクリプトで結合）にそのまま載せられる。フラグメントは
セクター名・銘柄コードのみを指定し、数値は CSV からルックアップするため、
転記ミスは構造的に排除される（一致しなければ非ゼロ終了）。

標準ライブラリのみ（requests 等は不要・キー不要）。

使い方:
  python scripts/build_market_json.py \
      --date 2026-07-01 \
      --csv-dir ../test-jquants/output \
      --narrative /path/to/market_narrative_2026-07-01.json \
      --out docs/data/2026-07-01_market.json
"""
import argparse
import csv
import json
import os
import sys
import unicodedata

SCHEMA_VERSION = 1


def die(msg):
    sys.stderr.write("[build_market_json] ERROR: " + msg + "\n")
    raise SystemExit(1)


def parse_num(s):
    """'+15.09' / '-0.64' / '"1,535.3"' / '58,619.1' → float。空は None。"""
    if s is None:
        return None
    s = str(s).strip().strip('"').replace(",", "")
    if s == "" or s == "-" or s == "—":
        return None
    try:
        return float(s)
    except ValueError:
        die("数値として解釈できない値: %r" % s)


def nfkc(s):
    return unicodedata.normalize("NFKC", (s or "").strip())


def read_sector_csv(path):
    """sector_return_<date>.csv → 33業種の行リスト（CSV 順＝加重降順を維持）。"""
    if not os.path.exists(path):
        die("sector CSV が見つからない: %s" % path)
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = (r.get("33業種") or "").strip()
            if not name:
                continue
            rows.append({
                "name": name,
                "n": int(parse_num(r.get("銘柄数")) or 0),
                "up": int(parse_num(r.get("値上がり")) or 0),
                "down": int(parse_num(r.get("値下がり")) or 0),
                "flat": int(parse_num(r.get("変わらず")) or 0),
                "w_pct": parse_num(r.get("売買代金加重騰落率%")),
                "mean_pct": parse_num(r.get("単純平均騰落率%")),
                "median_pct": parse_num(r.get("中央値騰落率%")),
                "turnover_oku": parse_num(r.get("売買代金合計(億円)")),
            })
    return rows


def read_movers_csv(path):
    """movers_top_<date>.csv → {'値上がり': {code: row}, '値下がり': {code: row}}。

    コードは英数字混在（例 429A/330A）があるため文字列で扱う。
    銘柄名は NFKC 正規化で全角英数を半角化する。
    """
    if not os.path.exists(path):
        die("movers CSV が見つからない: %s" % path)
    idx = {"値上がり": {}, "値下がり": {}}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            side = (r.get("区分") or "").strip()
            code = (r.get("証券コード") or "").strip()
            if side not in idx or not code:
                continue
            idx[side][code] = {
                "code": code,
                "name": nfkc(r.get("銘柄名")),
                "sector33": (r.get("33業種") or "").strip(),
                "close": parse_num(r.get("当日終値")),
                "pct": parse_num(r.get("前日比%")),
                "turnover_oku": parse_num(r.get("売買代金(億円)")),
            }
    return idx


def require(cond, msg):
    if not cond:
        die(msg)


def check_url(u, ctx):
    if not isinstance(u, str) or not (u.startswith("http://") or u.startswith("https://")):
        die("URL は http(s) のみ許可（%s）: %r" % (ctx, u))


def check_links(links, ctx):
    for lk in (links or []):
        check_url(lk.get("url"), ctx)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _oku_jp(oku):
    """億円 → 表示文字列。1兆（10,000億）以上は「約X.X兆円」。"""
    if oku is None:
        return "-"
    if oku >= 10000:
        return "約%.1f兆円" % (oku / 10000.0)
    return "%s億円" % format(int(round(oku)), ",")


def fill_snapshot_row(row, stats):
    """overview.snapshot の "auto" 行を stats から機械生成する（数値手書きの排除）。

    フラグメントが label/value を明示していればそれを優先（auto は欠けた欄のみ補完）。
    """
    kind = row.get("auto")
    if not kind:
        return row
    label = row.get("label")
    value = row.get("value")
    if kind == "topix":
        tp = stats.get("topix_pct")
        cl = stats.get("topix_close")
        label = label or "TOPIX（終値ベース）"
        if value is None:
            value = ("%+.2f%%" % tp) if tp is not None else "取得不可"
            if tp is not None and cl is not None:
                value += " %s" % format(cl, ",")
    elif kind == "breadth":
        b = stats.get("breadth") or {}
        label = label or "値上がり / 値下がり / 変わらず"
        if value is None:
            value = "%s / %s / %s" % (
                format(b.get("up", 0), ","), format(b.get("down", 0), ","),
                format(b.get("flat", 0), ","))
    elif kind == "top_sector":
        ts = stats.get("top_sector_by_turnover") or {}
        label = label or "最大売買代金セクター"
        if value is None:
            value = "%s %s" % (ts.get("name", ""), _oku_jp(ts.get("turnover_oku")))
    elif kind == "top_stock":
        ts = stats.get("top_stock_by_turnover") or {}
        label = label or "最大売買代金銘柄"
        if value is None:
            value = "%s %s" % (ts.get("name", ""), _oku_jp(ts.get("turnover_oku")))
    else:
        die("overview.snapshot の auto 種別が不正: %r" % kind)
    # 出力キー順は {label, value, note, ...} で固定（既存スキーマに整合）
    out = {"label": label, "value": value}
    for k, v in row.items():
        if k not in ("auto", "label", "value"):
            out[k] = v
    return out


def top_stock_out(ts):
    """market.json の top_stock_by_turnover は {code,name,turnover_oku} で固定（既存スキーマ）。"""
    if not ts:
        return None
    return {"code": ts.get("code"), "name": ts.get("name"), "turnover_oku": ts.get("turnover_oku")}


def main():
    ap = argparse.ArgumentParser(description="市場分析 Web 配信用 JSON を組み立てる")
    ap.add_argument("--date", required=True, help="セッション日 YYYY-MM-DD")
    ap.add_argument("--csv-dir", required=True, help="sector_return/movers_top CSV のディレクトリ")
    ap.add_argument("--narrative", required=True, help="ナラティブ・フラグメント JSON")
    ap.add_argument("--stats", default=None,
                    help="build_market_stats.py の market_stats JSON（CSV外の決定的数値）")
    ap.add_argument("--defaults", default=None,
                    help="静的テンプレ（title/universe/methodology/disclaimer）JSON")
    ap.add_argument("--out", required=True, help="出力先 <date>_market.json")
    args = ap.parse_args()

    date = args.date
    sector_path = os.path.join(args.csv_dir, "sector_return_%s.csv" % date)
    movers_path = os.path.join(args.csv_dir, "movers_top_%s.csv" % date)

    sectors = read_sector_csv(sector_path)
    require(len(sectors) == 33, "sector CSV は33行であるべき（実際: %d 行）" % len(sectors))
    sec_by_name = {s["name"]: s for s in sectors}

    movers = read_movers_csv(movers_path)

    frag = load_json(args.narrative)
    # 静的テンプレ（defaults）を土台に、フラグメントで上書き（浅いマージ）。
    if args.defaults:
        base = load_json(args.defaults)
        base.update(frag)
        frag = base

    stats = load_json(args.stats) if args.stats else None
    if stats:
        require(stats.get("session_date") == date,
                "stats の session_date(%s) が --date(%s) と不一致" % (stats.get("session_date"), date))

    if frag.get("session_date"):
        require(frag["session_date"] == date,
                "フラグメントの session_date(%s) が --date(%s) と不一致" % (frag["session_date"], date))

    # ── 決定的パート（CSV から算出）
    up = sum(s["up"] for s in sectors)
    down = sum(s["down"] for s in sectors)
    flat = sum(s["flat"] for s in sectors)
    n_liquid = sum(s["n"] for s in sectors)
    require(up + down + flat == n_liquid,
            "騰落数合計(%d) と 銘柄数合計(%d) が不一致" % (up + down + flat, n_liquid))

    top_sector = max(sectors, key=lambda s: (s["turnover_oku"] or 0))
    # 最大売買代金銘柄は movers（値上がり＋値下がり）の中の最大で近似する（--stats 無しの手動範囲）。
    all_movers = list(movers["値上がり"].values()) + list(movers["値下がり"].values())
    top_stock = max(all_movers, key=lambda m: (m["turnover_oku"] or 0)) if all_movers else None

    # ── stats（build_market_stats.py）との整合チェック＋決定的数値の採用
    topix_pct = frag.get("topix_pct")
    prev_date = frag.get("prev_date")
    generated_at = frag.get("generated_at")
    sources_accessed = frag.get("sources_accessed", "")
    if stats:
        sb = stats.get("breadth") or {}
        require(sb.get("up") == up and sb.get("down") == down and sb.get("flat") == flat,
                "stats.breadth(%s) が CSV 集計(%d-%d-%d) と不一致" % (sb, up, down, flat))
        su = (stats.get("universe") or {}).get("n_liquid")
        require(su is None or su == n_liquid,
                "stats.universe.n_liquid(%s) が CSV 集計(%d) と不一致" % (su, n_liquid))
        if stats.get("top_stock_by_turnover"):
            top_stock = stats["top_stock_by_turnover"]  # 全ユニバース真値（movers 近似より優先）
        s_topix = stats.get("topix_pct")
        if s_topix is not None and topix_pct is not None and abs(s_topix - topix_pct) > 0.01:
            die("topix_pct が stats(%.2f) と フラグメント(%.2f) で不一致（古いフラグメント？）"
                % (s_topix, topix_pct))
        if s_topix is not None:
            topix_pct = s_topix
        prev_date = stats.get("prev_date") or prev_date
        generated_at = stats.get("generated_at") or generated_at
    if not sources_accessed and generated_at:
        sources_accessed = generated_at[:10]

    # ── strip：フラグメントはセクター名のみ、pct を join
    def strip_side(names):
        out = []
        for nm in (names or []):
            require(nm in sec_by_name, "strip のセクター名が CSV に無い: %r" % nm)
            out.append({"name": nm, "pct": sec_by_name[nm]["w_pct"]})
        return out

    strip = {
        "sectors_up": strip_side((frag.get("strip") or {}).get("sectors_up")),
        "sectors_down": strip_side((frag.get("strip") or {}).get("sectors_down")),
    }

    # ── sector_flags：フラグメントの {name: mark} を sectors33 に付与
    flags = frag.get("sector_flags") or {}
    for nm in flags:
        require(nm in sec_by_name, "sector_flags のセクター名が CSV に無い: %r" % nm)
    for s in sectors:
        if s["name"] in flags:
            s["flag"] = flags[s["name"]]

    # ── bought / sold：フラグメント {sector, note, flag?} に数値を join
    def build_side(side):
        side = side or {}
        table = []
        for row in (side.get("table") or []):
            nm = row.get("sector")
            require(nm in sec_by_name, "bought/sold のセクター名が CSV に無い: %r" % nm)
            s = sec_by_name[nm]
            item = {
                "sector": nm,
                "w_pct": s["w_pct"],
                "median_pct": s["median_pct"],
                "up": s["up"],
                "down": s["down"],
                "note": row.get("note", ""),
            }
            if row.get("flag"):
                item["flag"] = row["flag"]
            table.append(item)
        return {"table": table, "themes": side.get("themes") or []}

    bought = build_side(frag.get("bought"))
    sold = build_side(frag.get("sold"))

    # ── movers：フラグメント {code, note, links, emph} に CSV の数値を join
    def build_movers(items, side_key):
        pool = movers[side_key]
        out = []
        for row in (items or []):
            code = str(row.get("code", "")).strip()
            require(code in pool, "movers(%s) の code が CSV に無い: %r" % (side_key, code))
            m = pool[code]
            check_links(row.get("links"), "movers %s %s" % (side_key, code))
            item = {
                "code": m["code"],
                "name": m["name"],
                "pct": m["pct"],
                "close": m["close"],
                "turnover_oku": m["turnover_oku"],
                "sector33": m["sector33"],
                "note": row.get("note", ""),
                "links": row.get("links") or [],
            }
            if row.get("emph"):
                item["emph"] = True
            out.append(item)
        return out

    fm = frag.get("movers") or {}
    movers_out = {
        "gainers": build_movers(fm.get("gainers"), "値上がり"),
        "gainers_footnote": fm.get("gainers_footnote", ""),
        "losers": build_movers(fm.get("losers"), "値下がり"),
        "losers_footnote": fm.get("losers_footnote", ""),
    }

    # ── news_sources の URL 検査
    for ns in (frag.get("news_sources") or []):
        check_links(ns.get("links"), "news_sources %s" % ns.get("topic"))

    # ── overview.snapshot の auto 行を stats から機械生成（{n_liquid} も置換）
    def sub_n(s):
        return s.replace("{n_liquid}", format(n_liquid, ",")) if isinstance(s, str) else s

    overview = frag.get("overview") or {}
    if stats and isinstance(overview.get("snapshot"), list):
        overview = dict(overview)
        overview["snapshot"] = [fill_snapshot_row(r, stats) for r in overview["snapshot"]]

    methodology = frag.get("methodology") or {}
    if isinstance(methodology.get("lines"), list):
        methodology = dict(methodology)
        methodology["lines"] = [sub_n(ln) for ln in methodology["lines"]]

    universe = frag.get("universe") or {}
    out = {
        "schema_version": SCHEMA_VERSION,
        "kind": "market_analysis",
        "session_date": date,
        "prev_date": prev_date,
        "generated_at": generated_at,
        "title": frag.get("title", "東京株式市場 セクター/テーマ 騰落率分析"),
        "universe": {
            "description": sub_n(universe.get("description", "")),
            "min_turnover_yen": universe.get("min_turnover_yen", 100000000),
            "n_liquid": n_liquid,
        },
        "market": {
            "topix_pct": topix_pct,
            "breadth": {"up": up, "down": down, "flat": flat},
            "top_sector_by_turnover": {"name": top_sector["name"], "turnover_oku": top_sector["turnover_oku"]},
            "top_stock_by_turnover": top_stock_out(top_stock),
        },
        "thesis": frag.get("thesis", ""),
        "strip": strip,
        "overview": overview,
        "sectors33": sectors,
        "sector_notes": frag.get("sector_notes") or [],
        "bought": bought,
        "sold": sold,
        "movers": movers_out,
        "theme_matrix": frag.get("theme_matrix") or {},
        "methodology": methodology,
        "news_sources": frag.get("news_sources") or [],
        "sources_accessed": sources_accessed,
        "disclaimer": frag.get("disclaimer") or [],
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")

    sys.stderr.write(
        "[build_market_json] OK: %s（33業種 / breadth %d-%d-%d=%d / gainers %d / losers %d）\n"
        % (args.out, up, down, flat, n_liquid,
           len(movers_out["gainers"]), len(movers_out["losers"]))
    )


if __name__ == "__main__":
    main()
