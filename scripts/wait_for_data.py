"""データ鮮度ガード付きの営業日ゲート（ルーチン step1）。

check_gate.py（純・ネット無しの営業日判定）を置き換える起点。当日 D が東証営業日なら、
J-Quants の当日 `/equities/bars/daily`（四本値）が「確定・ほぼ全銘柄そろった」状態に
なるまでポーリングで待ってから `SESSION=D` を出す。休場日は即 `SKIP`（ネット呼び出しゼロ）。

狙い：ルーチンを 16:35 JST に前倒ししつつ、四本値の当日反映（公式「16:30頃」・実際は前後）を
待つ適応型ゲートにする。通常日は16:30台に確定→即続行、遅延日のみ待機。打ち切りは 18:10 JST
（＝旧起動時刻）の壁時計で、「現状より遅くしない／現行が配信できた日を取りこぼさない」を保証。

判定（プローブ優先で安価に）:
  1) プローブ（安価）: jquants.last_confirmed_session(D)==D（当日終値 C が確定）。
  2) 件数完全性（bars）: len(bars_by_date(D)) >= ratio * len(bars_by_date(前営業日))。
  3) 件数完全性（master・保険）: len(master_by_date(D)) >= ratio * len(master_by_date(前営業日))。
  ratio は早期確定 --ready-ratio（既定0.95）／締切ハードフロア --floor-ratio（既定0.90）。
  build_day_ranking.py には空/部分データの自己防御が無いため、部分公開を件数比で弾く。

出力（stdout は必ず1トークン行のみ。進捗・WARN・エラーは stderr）:
  SKIP              休場日（exit 0）
  SESSION=YYYY-MM-DD  当日データ確定 or 締切時に準完全（exit 0。準完全時は stderr WARN）
  TIMEOUT           締切までに四本値が未到達＝配信しない（exit 2、--on-timeout=skip 既定）
  （APIキー未設定は stderr にエラーを出して exit 1・stdout 無し）

usage:
  python scripts/wait_for_data.py [YYYY-MM-DD]
     [--interval 30] [--max-wait 5700] [--not-later-than 18:10]
     [--ready-ratio 0.95] [--floor-ratio 0.90]
     [--on-timeout skip|continue] [--once]
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import business_day
import jquants

JST = timezone(timedelta(hours=9))


def elog(*a):
    """stdout を1トークンに保つため、進捗・警告・エラーは stderr へ。"""
    print(*a, file=sys.stderr, flush=True)


def _prev_counts(session_iso):
    """前営業日の bars/master 件数（完全性比較の分母）。取得不能は 0 を返す。"""
    prev_iso = business_day.prev_business_day(date.fromisoformat(session_iso)).isoformat()
    try:
        pb = len(jquants.bars_by_date(prev_iso))
        pm = len(jquants.master_by_date(prev_iso))
    except Exception as e:
        elog("[wait_for_data] WARN 前営業日件数の取得に失敗（件数チェックをプローブのみに縮退）: %s" % e)
        return prev_iso, 0, 0
    return prev_iso, pb, pm


def evaluate(session_iso, prev_bars_n, prev_master_n, ready_ratio, floor_ratio):
    """当日データの確定度を1回評価する。

    返り値 dict: strict（早期確定=ready比達成）／near（締切許容=floor比達成）／
                 probe_ok／bars_ratio／master_ratio／bars_n／master_n。
    プローブ不通過なら重い全件取得はしない（API負荷最小化）。
    """
    try:
        last = jquants.last_confirmed_session(session_iso)
    except Exception as e:
        elog("[wait_for_data] WARN プローブ取得に失敗: %s" % e)
        return dict(strict=False, near=False, probe_ok=False,
                    bars_ratio=None, master_ratio=None, bars_n=None, master_n=None)
    if last != session_iso:
        return dict(strict=False, near=False, probe_ok=False,
                    bars_ratio=None, master_ratio=None, bars_n=None, master_n=None)

    # プローブ通過 → 全件件数で完全性を見る。
    bars_n = len(jquants.bars_by_date(session_iso))
    master_n = len(jquants.master_by_date(session_iso))
    # 前営業日件数が取れないときは件数チェックを無効化（プローブ通過で pass 扱い）。
    bars_ratio = (bars_n / prev_bars_n) if prev_bars_n else 1.0
    master_ratio = (master_n / prev_master_n) if prev_master_n else 1.0
    strict = bars_ratio >= ready_ratio and master_ratio >= ready_ratio
    near = bars_ratio >= floor_ratio and master_ratio >= floor_ratio
    return dict(strict=strict, near=near, probe_ok=True,
                bars_ratio=bars_ratio, master_ratio=master_ratio,
                bars_n=bars_n, master_n=master_n)


def _ratio_str(info):
    def pct(x):
        return "n/a" if x is None else ("%.1f%%" % (x * 100))
    return "probe=%s bars=%s master=%s" % (
        "OK" if info["probe_ok"] else "NG", pct(info["bars_ratio"]), pct(info["master_ratio"]))


def emit_session_and_exit(session_iso, warn=None):
    if warn:
        elog("[wait_for_data] WARN %s" % warn)
    print("SESSION=%s" % session_iso)   # ← stdout（1トークン）
    return 0


def resolve_deadline(now_jst, max_wait, not_later_than):
    """deadline（monotonic秒）を min(now+max_wait, 本日 not_later_than JST) で決める。

    既に締切時刻を過ぎていれば実効待機0＝ループは1回だけ評価して打ち切りへ。
    """
    hh, mm = (int(x) for x in not_later_than.split(":"))
    cutoff_wall = now_jst.replace(hour=hh, minute=mm, second=0, microsecond=0)
    secs_to_cutoff = (cutoff_wall - now_jst).total_seconds()
    effective = min(max_wait, max(0.0, secs_to_cutoff))
    return time.monotonic() + effective, cutoff_wall, effective


def main():
    ap = argparse.ArgumentParser(description="データ鮮度ガード付き営業日ゲート")
    ap.add_argument("date", nargs="?", help="対象日 YYYY-MM-DD（省略時は JST の当日）")
    ap.add_argument("--interval", type=float, default=30.0, help="ポーリング間隔秒（既定30）")
    ap.add_argument("--max-wait", type=float, default=5700.0,
                    help="最大待機秒（副次上限。既定5700＝16:35起動から18:10壁時計に一致。"
                         "実効締切は min(起動+max_wait, --not-later-than) で、通常は18:10壁時計が binding）")
    ap.add_argument("--not-later-than", default="18:10",
                    help="壁時計の打ち切り時刻 HH:MM（JST。既定18:10＝旧起動時刻）")
    ap.add_argument("--ready-ratio", type=float, default=0.95,
                    help="早期確定の当日件数/前日件数 下限（既定0.95）")
    ap.add_argument("--floor-ratio", type=float, default=0.90,
                    help="締切時に許容する当日件数/前日件数 ハードフロア（既定0.90）")
    ap.add_argument("--on-timeout", choices=("skip", "continue"), default="skip",
                    help="締切時に未到達なら skip=TIMEOUT（配信しない・既定）／continue=不完全でも続行")
    ap.add_argument("--once", action="store_true", help="1回だけ評価（sleep無し・テスト用）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    # 1) 営業日ゲート（休場は即 SKIP・ネット呼び出しゼロ・APIキー不要＝check_gate.py と同挙動）。
    run_day = date.fromisoformat(args.date) if args.date else datetime.now(JST).date()
    session_d = business_day.tse_session_date_for(run_day)
    if session_d is None:
        print("SKIP")   # ← stdout
        return 0
    session_iso = session_d.isoformat()

    # 営業日は当日データ取得が必要＝APIキー必須。
    if not os.environ.get("JQUANTS_API_KEY"):
        elog("[wait_for_data] ERROR JQUANTS_API_KEY 未設定")
        return 1

    # 2) 完全性比較の分母（前営業日件数）を一度だけ取得。
    prev_iso, prev_bars_n, prev_master_n = _prev_counts(session_iso)
    elog("[wait_for_data] session=%s prev=%s prev_bars=%d prev_master=%d "
         "ready>=%.2f floor>=%.2f interval=%.0fs on_timeout=%s"
         % (session_iso, prev_iso, prev_bars_n, prev_master_n,
            args.ready_ratio, args.floor_ratio, args.interval, args.on_timeout))

    now_jst = datetime.now(JST)
    deadline_mono, cutoff_wall, effective = resolve_deadline(now_jst, args.max_wait, args.not_later_than)
    elog("[wait_for_data] cutoff=%s JST（実効待機 最大%.0f秒）"
         % (cutoff_wall.strftime("%Y-%m-%d %H:%M"), effective))

    # 3) ポーリング（プローブ→件数）。strict で即続行、締切で near 判定。
    n = 0
    info = None
    while True:
        n += 1
        info = evaluate(session_iso, prev_bars_n, prev_master_n, args.ready_ratio, args.floor_ratio)
        if info["strict"]:
            elog("[wait_for_data] 確定（checks=%d, %s）" % (n, _ratio_str(info)))
            return emit_session_and_exit(session_iso)
        if args.once or time.monotonic() >= deadline_mono:
            break
        elog("[wait_for_data] 未確定（checks=%d, %s）→ %.0fs 待機" % (n, _ratio_str(info), args.interval))
        time.sleep(args.interval)

    # 4) 締切（または --once）時の判定。
    if info["near"]:
        return emit_session_and_exit(
            session_iso,
            warn="締切時に件数が早期確定閾値未満だが floor は満たすため続行（旧18:10と同等の準完全許容, %s）"
                 % _ratio_str(info))
    if args.on_timeout == "continue":
        return emit_session_and_exit(
            session_iso, warn="締切までに未到達だが --on-timeout=continue のため続行（%s）" % _ratio_str(info))

    elog("[wait_for_data] ERROR 締切 %s JST までに当日四本値が到達せず（%s）。配信しない＝J-Quants遅延/障害を疑う。"
         % (cutoff_wall.strftime("%H:%M"), _ratio_str(info)))
    print("TIMEOUT")   # ← stdout
    return 2


if __name__ == "__main__":
    sys.exit(main())
