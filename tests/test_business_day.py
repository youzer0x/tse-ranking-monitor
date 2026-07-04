"""business_day.py（東証営業日・セッション日の判定）の単体テスト。

日付はすべて過去の確定日を固定値で渡す（date.today() に依存させない）。
祝日（jpholiday 由来）の判定は jpholiday が入っている時だけ検証する。
"""
from datetime import date

import pytest

import business_day as bd


def test_is_business_day_weekday():
    # 2026-07-03 は金曜（祝日でない）＝営業日
    assert bd.is_business_day(date(2026, 7, 3)) is True


def test_is_business_day_weekend():
    assert bd.is_business_day(date(2026, 7, 4)) is False   # 土
    assert bd.is_business_day(date(2026, 7, 5)) is False   # 日


def test_is_business_day_year_end_new_year():
    # 12/31〜1/3 は東証休場（jpholiday 非依存の固定ルール）
    for d in [date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3)]:
        assert bd.is_business_day(d) is False


@pytest.mark.skipif(not bd._HAS_JP, reason="jpholiday 未導入時は祝日を判定できない")
def test_is_business_day_national_holiday():
    # 2026-07-20（海の日・月）と 2026-05-05（こどもの日・火）は平日だが休場
    assert bd.is_business_day(date(2026, 7, 20)) is False
    assert bd.is_business_day(date(2026, 5, 5)) is False


def test_prev_business_day_skips_weekend():
    # 月曜の直前営業日は前週金曜
    assert bd.prev_business_day(date(2026, 7, 6)) == date(2026, 7, 3)


@pytest.mark.skipif(not bd._HAS_JP, reason="jpholiday 未導入時は祝日をまたげない")
def test_prev_business_day_skips_holiday_and_weekend():
    # 2026-07-21(火) の直前営業日：07-20(海の日) 07-19(日) 07-18(土) を飛ばして 07-17(金)
    assert bd.prev_business_day(date(2026, 7, 21)) == date(2026, 7, 17)


def test_nth_prev_business_day():
    # 07-06(月) の2営業日前：prev→07-03(金), prev→07-02(木)
    assert bd.nth_prev_business_day(date(2026, 7, 6), 2) == date(2026, 7, 2)


def test_tse_session_date_for_business_day():
    # 営業日に実行した日のセッション日はその日自身
    assert bd.tse_session_date_for(date(2026, 7, 3)) == date(2026, 7, 3)


def test_tse_session_date_for_holiday_returns_none():
    # 休場日に実行 → 新規セッション無し → None（ルーチンはスキップ）
    assert bd.tse_session_date_for(date(2026, 7, 4)) is None       # 土
    if bd._HAS_JP:
        assert bd.tse_session_date_for(date(2026, 7, 20)) is None  # 海の日
