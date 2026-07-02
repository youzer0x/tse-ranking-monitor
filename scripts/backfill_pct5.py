"""既存の docs/data/YYYY-MM-DD.json に 5営業日騰落率（pct5）を後追い付与する。

各セッション日について当日と5営業日前の bars/daily を一括取得し、
rows / dropped_turnover / dropped_mcap の各行に
  pct5 = round(AdjC(当日) / AdjC(5営業日前) − 1, 2) * 100   （欠損は None）
を書き戻す。併せて prev5_date を付与する。冪等（再実行で上書き）。

build_day_ranking.py の pct5 算出と同一定義（5営業日前比）。取得は jquants.bars_by_date、
営業日計算は business_day.nth_prev_business_day を再利用する。要 JQUANTS_API_KEY。

usage:
  python backfill_pct5.py [--data-dir docs/data] [--date YYYY-MM-DD ...] [--dry-run]
  （--date 省略時は data-dir 内の全 YYYY-MM-DD.json。manifest.json / *_market.json は除外）
"""
import sys, os, json, glob, argparse
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jquants, business_day


def backfill_file(path, dry_run=False, verbose=True):
    def log(*a):
        if verbose:
            print(*a, file=sys.stderr)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    session_iso = data.get("session_date") or data.get("date")  # 旧形式は "date"
    if not session_iso:
        log(f"# skip {os.path.basename(path)}: session_date/date 無し")
        return False
    session_d = date.fromisoformat(session_iso)
    prev5_iso = business_day.nth_prev_business_day(session_d, 5).isoformat()
    bars_now = jquants.bars_by_date(session_iso)
    bars_prev5 = jquants.bars_by_date(prev5_iso)

    def adjc(bars, code):
        b = bars.get(jquants.code5(str(code)))
        return b.get("AdjC") if b else None

    def pct5_for(code):
        a = adjc(bars_now, code)
        a5 = adjc(bars_prev5, code)
        return round((a / a5 - 1.0) * 100.0, 2) if (a and a5) else None

    n = 0
    # rows は新形式 "rows"、旧形式は "items"。除外表は両形式共通。
    rowlists = [data.get("rows") or data.get("items") or [],
                data.get("dropped_turnover") or [],
                data.get("dropped_mcap") or []]
    for rl in rowlists:
        for r in rl:
            if not isinstance(r, dict) or "code" not in r:
                continue
            r["pct5"] = pct5_for(r["code"])
            n += 1
    data["prev5_date"] = prev5_iso

    name = os.path.basename(path)
    if dry_run:
        log(f"# [dry-run] {name}: session={session_iso} prev5={prev5_iso} rows_updated={n}")
        return True
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
    log(f"# wrote {name}: session={session_iso} prev5={prev5_iso} rows_updated={n}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None,
                    help="docs/data ディレクトリ（省略時はリポジトリ内 docs/data）")
    ap.add_argument("--date", action="append",
                    help="対象セッション日 YYYY-MM-DD（複数可・省略時は全 *.json）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sys.stderr.reconfigure(encoding="utf-8")
    if args.data_dir:
        data_dir = args.data_dir
    else:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(repo, "docs", "data")

    if args.date:
        paths = [os.path.join(data_dir, d + ".json") for d in args.date]
    else:
        paths = sorted(
            p for p in glob.glob(os.path.join(data_dir, "*.json"))
            if os.path.basename(p) != "manifest.json"
            and not os.path.basename(p).endswith("_market.json")
        )

    ok = 0
    for p in paths:
        if not os.path.exists(p):
            print(f"# missing: {p}", file=sys.stderr)
            continue
        if backfill_file(p, dry_run=args.dry_run):
            ok += 1
    print(f"# done: {ok}/{len(paths)} files", file=sys.stderr)


if __name__ == "__main__":
    main()
