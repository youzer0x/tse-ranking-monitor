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
  3) master 件数も取得して診断表示するが、当日barsだけを必須の合否判定に使う。
  ratio は早期確定 --ready-ratio（既定0.95）／締切ハードフロア --floor-ratio（既定0.90）。
  前日barsの基準件数または当日bars全件が取得不能なら「未準備」として再試行する。

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
import json
import os
import sys
import time
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path

# 共有ベンダーは scripts/ 配置を維持する。パッケージを直接 import した場合も解決できるようにする。
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import business_day
import jquants

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "data" / "manifest.json"
SESSION_CLOSE = wall_time(15, 30)


def elog(*a):
    """stdout を1トークンに保つため、進捗・警告・エラーは stderr へ。"""
    print(*a, file=sys.stderr, flush=True)


def _prev_counts(session_iso):
    """前営業日の bars/master 件数を独立取得する。

    bars は合否判定の必須baseline、masterは診断専用である。どちらかの取得失敗で
    もう一方の成功を捨てず、取得不能は ``None`` として呼び出し側の再試行対象にする。
    """
    prev_iso = business_day.prev_business_day(date.fromisoformat(session_iso)).isoformat()
    try:
        pb = len(jquants.bars_by_date(prev_iso))
    except Exception as e:
        elog("[wait_for_data] WARN 前営業日bars件数の取得に失敗（未準備として再試行）: %s" % e)
        pb = None
    if not pb:
        pb = None
    try:
        pm = len(jquants.master_by_date(prev_iso))
    except Exception as e:
        elog("[wait_for_data] WARN 前営業日master件数の取得に失敗（診断値のみ欠落）: %s" % e)
        pm = None
    if not pm:
        pm = None
    return prev_iso, pb, pm


def evaluate(session_iso, prev_bars_n, prev_master_n, ready_ratio, floor_ratio):
    """当日データの確定度を1回評価する。

    返り値 dict: strict（早期確定=ready比達成）／near（締切許容=floor比達成）／
                 probe_ok／bars_ratio／master_ratio／bars_n／master_n。
    プローブ不通過なら重い全件取得はしない（API負荷最小化）。bars のbaseline欠落・
    当日全件取得例外は pass へ縮退せず retry可能な not-ready として返す。
    master は取得・比率表示するが strict/near の合否には影響しない。
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

    # プローブ通過 → 全件件数で完全性を見る。当日bars例外はポーリング継続可能にする。
    try:
        bars_n = len(jquants.bars_by_date(session_iso))
    except Exception as e:
        elog("[wait_for_data] WARN 当日bars全件の取得に失敗（未準備として再試行）: %s" % e)
        return dict(strict=False, near=False, probe_ok=True,
                    bars_ratio=None, master_ratio=None, bars_n=None, master_n=None)

    # master は診断情報。取得失敗や比率低下がbarsの合格を覆さない。
    try:
        master_n = len(jquants.master_by_date(session_iso))
    except Exception as e:
        elog("[wait_for_data] WARN 当日master件数の取得に失敗（診断値のみ欠落）: %s" % e)
        master_n = None

    bars_ratio = (bars_n / prev_bars_n) if prev_bars_n else None
    master_ratio = (master_n / prev_master_n) if (master_n is not None and prev_master_n) else None
    strict = bars_ratio is not None and bars_ratio >= ready_ratio
    near = bars_ratio is not None and bars_ratio >= floor_ratio
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


def read_published_dates(manifest_path=DEFAULT_MANIFEST):
    """Return the validated session dates listed in the local Pages manifest.

    A missing manifest represents a repository with no published history.  A
    malformed manifest fails closed: silently ignoring corrupt publication
    state could select and republish the wrong session.
    """
    path = Path(manifest_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("manifest could not be read: %s" % exc) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), list):
        raise ValueError("manifest.dates must be an array")

    published = []
    for value in payload["dates"]:
        if not isinstance(value, str):
            raise ValueError("manifest.dates entries must be YYYY-MM-DD strings")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("invalid manifest date: %s" % value) from exc
        if parsed.isoformat() != value:
            raise ValueError("manifest date must use canonical YYYY-MM-DD: %s" % value)
        published.append(parsed)
    return published


def latest_completed_session(now_jst):
    """Return the newest TSE session whose 15:30 close has passed in JST."""
    today = now_jst.astimezone(JST).date() if now_jst.tzinfo else now_jst.date()
    local_time = now_jst.astimezone(JST).time() if now_jst.tzinfo else now_jst.time()
    if business_day.is_business_day(today) and local_time >= SESSION_CLOSE:
        return today
    return business_day.prev_business_day(today)


def select_target_session(now_jst, published_dates):
    """Choose the oldest unpublished, already-completed business session.

    Publication history is intentionally treated as a high-water mark.  Gaps
    older than the latest published date are not rewritten, which preserves
    the repository's immutable historical publication boundary.
    """
    completed = latest_completed_session(now_jst)
    latest = max(published_dates, default=None)
    if latest is None:
        return completed
    candidate = latest + timedelta(days=1)
    while candidate <= completed:
        if business_day.is_business_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    return None


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
    ap.add_argument("date", nargs="?", help="対象日 YYYY-MM-DD（省略時は公開manifestから自動選択）")
    ap.add_argument("--date", dest="date_option",
                    help="対象日 YYYY-MM-DD（位置引数と同じ明示指定）")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="自動選択に使う公開manifest（既定 docs/data/manifest.json）")
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

    if args.date and args.date_option:
        ap.error("対象日は位置引数または --date のどちらか一方だけを指定")

    # 1) 明示日付は従来どおり営業日だけを対象にする。省略時は公開済みの
    # high-water mark から、完了済みセッションの最古の未処理日を選ぶ。
    now_jst = datetime.now(JST)
    explicit_date = args.date_option or args.date
    if explicit_date:
        try:
            run_day = date.fromisoformat(explicit_date)
        except ValueError as exc:
            ap.error("date は YYYY-MM-DD 形式で指定: %s" % exc)
        session_d = business_day.tse_session_date_for(run_day)
        if session_d is None:
            print("SKIP")   # ← stdout
            return 0
    else:
        try:
            published_dates = read_published_dates(args.manifest)
        except ValueError as exc:
            elog("[wait_for_data] ERROR 公開manifest不正: %s" % exc)
            return 1
        session_d = select_target_session(now_jst, published_dates)
        if session_d is None:
            print("SKIP")   # 完了済みの未処理セッションなし
            return 0
    session_iso = session_d.isoformat()
    catch_up = session_d < now_jst.date()

    # 営業日は当日データ取得が必要＝APIキー必須。
    if not os.environ.get("JQUANTS_API_KEY"):
        elog("[wait_for_data] ERROR JQUANTS_API_KEY 未設定")
        return 1

    # 2) 完全性比較の分母。失敗時はポーリング中に再取得する。
    prev_iso, prev_bars_n, prev_master_n = _prev_counts(session_iso)
    elog("[wait_for_data] session=%s prev=%s prev_bars=%s prev_master=%s "
         "ready>=%.2f floor>=%.2f interval=%.0fs on_timeout=%s"
         % (session_iso, prev_iso, prev_bars_n if prev_bars_n is not None else "n/a",
            prev_master_n if prev_master_n is not None else "n/a",
            args.ready_ratio, args.floor_ratio, args.interval, args.on_timeout))

    if catch_up:
        # 過去セッションは既に確定済みであるべきなので、翌朝の時計を当日
        # データ待ちの18:10締切として解釈しない。1回評価し、不足ならTIMEOUT。
        deadline_mono = time.monotonic()
        cutoff_wall = now_jst
        effective = 0.0
        elog("[wait_for_data] catch-up=%s（完了済みセッションのため待機なし）" % session_iso)
    else:
        deadline_mono, cutoff_wall, effective = resolve_deadline(
            now_jst, args.max_wait, args.not_later_than)
        elog("[wait_for_data] cutoff=%s JST（実効待機 最大%.0f秒）"
             % (cutoff_wall.strftime("%Y-%m-%d %H:%M"), effective))

    # 3) ポーリング（プローブ→件数）。strict で即続行、締切で near 判定。
    n = 0
    info = None
    while True:
        n += 1
        if prev_bars_n is None or prev_master_n is None:
            _prev_iso, pb, pm = _prev_counts(session_iso)
            if pb is not None:
                prev_bars_n = pb
            if pm is not None:
                prev_master_n = pm
        info = evaluate(session_iso, prev_bars_n, prev_master_n, args.ready_ratio, args.floor_ratio)
        if info["strict"]:
            elog("[wait_for_data] 確定（checks=%d, %s）" % (n, _ratio_str(info)))
            return emit_session_and_exit(session_iso)
        if args.once or catch_up or time.monotonic() >= deadline_mono:
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
