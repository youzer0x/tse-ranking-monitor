"""営業日ゲート: 当日 D に生成すべき東証 日中（レギュラー）セッション日を判定して出力する。

  - 当日（D）が東証営業日 → `SESSION=YYYY-MM-DD`（=D）を出力し exit 0。
  - 当日が休場（=新規日中セッション無し） → `SKIP` を出力し exit 0。

ルーチンはこの出力を見て、SKIP のときは生成せず終了する。
PTS 版（前営業日ゲート・朝 06:06）と異なり、当日が営業日かを見る（cron 16:35 JST）。
※ルーチンの step1 は本ファイルを内包した scripts/wait_for_data.py（当日四本値の鮮度ガード付き）を使う。
  本ファイルは純・ネット無しの営業日プローブとして手動確認/フォールバック用に残す。
TZ=Asia/Tokyo を前提（クラウド環境変数で設定）。
"""
import argparse
from datetime import date

try:
    from scripts import business_day
except ModuleNotFoundError:
    import business_day


def main(argv=None):
    parser = argparse.ArgumentParser(description="ネット接続なしで東証営業日を判定")
    parser.add_argument("date", nargs="?", help="対象日 YYYY-MM-DD（省略時は当日）")
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(args.date) if args.date else date.today()
    except ValueError as exc:
        parser.error(f"date は YYYY-MM-DD 形式で指定: {exc}")
    s = business_day.tse_session_date_for(today)
    print(f"SESSION={s.isoformat()}" if s else "SKIP")
    return 0


if __name__ == "__main__":
    main()
