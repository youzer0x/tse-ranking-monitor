"""鮮度ゲートのbars必須判定と障害時フェイルセーフ。"""
import json
from datetime import date, datetime

import pytest

import wait_for_data as wfd


def _patch_probe(monkeypatch, value="2026-07-15"):
    monkeypatch.setattr(wfd.jquants, "last_confirmed_session", lambda _date: value)


def test_master_ratio_is_diagnostic_only(monkeypatch):
    _patch_probe(monkeypatch)
    monkeypatch.setattr(wfd.jquants, "bars_by_date", lambda _date: {str(i): {} for i in range(96)})
    monkeypatch.setattr(wfd.jquants, "master_by_date", lambda _date: {"1": {}})

    info = wfd.evaluate("2026-07-15", 100, 100, 0.95, 0.90)

    assert info["strict"] is True
    assert info["bars_ratio"] == 0.96
    assert info["master_ratio"] == 0.01


def test_missing_previous_bars_baseline_never_probe_only_passes(monkeypatch):
    _patch_probe(monkeypatch)
    monkeypatch.setattr(wfd.jquants, "bars_by_date", lambda _date: {"1": {}})
    monkeypatch.setattr(wfd.jquants, "master_by_date", lambda _date: {"1": {}})

    info = wfd.evaluate("2026-07-15", None, 1, 0.95, 0.90)

    assert info["probe_ok"] is True
    assert info["bars_ratio"] is None
    assert info["strict"] is False
    assert info["near"] is False


def test_session_full_bars_exception_is_retryable_not_ready(monkeypatch):
    _patch_probe(monkeypatch)

    def fail(_date):
        raise OSError("temporary API failure")

    monkeypatch.setattr(wfd.jquants, "bars_by_date", fail)

    info = wfd.evaluate("2026-07-15", 100, 100, 0.95, 0.90)

    assert info["probe_ok"] is True
    assert info["strict"] is False
    assert info["near"] is False
    assert info["bars_n"] is None


def test_session_master_exception_does_not_block_complete_bars(monkeypatch):
    _patch_probe(monkeypatch)
    monkeypatch.setattr(wfd.jquants, "bars_by_date", lambda _date: {str(i): {} for i in range(100)})
    monkeypatch.setattr(
        wfd.jquants, "master_by_date", lambda _date: (_ for _ in ()).throw(OSError("master unavailable")))

    info = wfd.evaluate("2026-07-15", 100, 100, 0.95, 0.90)

    assert info["strict"] is True
    assert info["master_n"] is None
    assert info["master_ratio"] is None


def test_main_retries_missing_previous_baseline_before_once_evaluation(monkeypatch, capsys):
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl.business_day, "tse_session_date_for", lambda d: d)
    calls = iter([
        ("2026-07-14", None, 100),
        ("2026-07-14", 100, 100),
    ])
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: next(calls))
    monkeypatch.setattr(
        wfd._impl, "evaluate",
        lambda _date, pb, pm, _ready, _floor: {
            "strict": pb == 100, "near": False, "probe_ok": True,
            "bars_ratio": 1.0 if pb else None, "master_ratio": 1.0,
            "bars_n": 100, "master_n": 100,
        })
    monkeypatch.setattr(wfd._impl.sys, "argv", ["wait_for_data.py", "2026-07-15", "--once"])

    assert wfd.main() == 0
    assert capsys.readouterr().out.strip() == "SESSION=2026-07-15"


def test_delayed_morning_run_selects_oldest_completed_gap_without_waiting(
        tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-07-13"]}), encoding="utf-8")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-07-15T06:44:00+09:00")
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(wfd._impl, "datetime", FixedDateTime)
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-07-13", 100, 100))
    seen = []

    def complete(session, *_args):
        seen.append(session)
        return {
            "strict": True, "near": True, "probe_ok": True,
            "bars_ratio": 1.0, "master_ratio": 1.0,
            "bars_n": 100, "master_n": 100,
        }

    monkeypatch.setattr(wfd._impl, "evaluate", complete)
    monkeypatch.setattr(
        wfd._impl.time, "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("catch-up must not sleep")),
    )
    monkeypatch.setattr(
        wfd._impl.sys, "argv",
        ["wait_for_data.py", "--manifest", str(manifest)],
    )

    assert wfd.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "SESSION=2026-07-14"
    assert "待機なし" in captured.err
    assert seen == ["2026-07-14"]


def test_no_unpublished_completed_session_skips_without_api_key(
        tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-07-14"]}), encoding="utf-8")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-07-15T06:44:00+09:00")
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(wfd._impl, "datetime", FixedDateTime)
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    monkeypatch.setattr(
        wfd._impl.sys, "argv",
        ["wait_for_data.py", "--manifest", str(manifest)],
    )

    assert wfd.main() == 0
    assert capsys.readouterr().out.strip() == "SKIP"


def test_explicit_date_option_preserves_manual_override(monkeypatch, capsys):
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-07-13", 100, 100))
    monkeypatch.setattr(
        wfd._impl, "evaluate",
        lambda *_args: {
            "strict": True, "near": True, "probe_ok": True,
            "bars_ratio": 1.0, "master_ratio": 1.0,
            "bars_n": 100, "master_n": 100,
        },
    )
    monkeypatch.setattr(
        wfd._impl.sys, "argv", ["wait_for_data.py", "--date", "2026-07-14"])

    assert wfd.main() == 0
    assert capsys.readouterr().out.strip() == "SESSION=2026-07-14"


def test_emitting_a_session_records_the_in_flight_pointer(
    monkeypatch, capsys, isolated_runtime_root
):
    """The pointer is how hook telemetry and the end-of-session guard learn the
    session; nothing exports it, so the gate must record it."""
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-07-13", 100, 100))
    monkeypatch.setattr(
        wfd._impl, "evaluate",
        lambda *_args: {
            "strict": True, "near": True, "probe_ok": True,
            "bars_ratio": 1.0, "master_ratio": 1.0,
            "bars_n": 100, "master_n": 100,
        },
    )
    monkeypatch.setattr(
        wfd._impl.sys, "argv", ["wait_for_data.py", "--date", "2026-07-14"])

    assert wfd.main() == 0
    assert capsys.readouterr().out.strip() == "SESSION=2026-07-14"

    pointer = json.loads(
        (isolated_runtime_root / ".work" / "_runtime" / "current_session.json")
        .read_text(encoding="utf-8")
    )
    assert pointer["session"] == "2026-07-14"


def test_skip_does_not_record_an_in_flight_pointer(monkeypatch, capsys, isolated_runtime_root):
    """A holiday must not leave a pointer, or the guard would alert for a day
    that was never supposed to produce anything."""
    monkeypatch.setattr(
        wfd._impl.sys, "argv", ["wait_for_data.py", "--date", "2026-07-19"])

    assert wfd.main() == 0
    assert capsys.readouterr().out.strip() == "SKIP"
    assert not (isolated_runtime_root / ".work" / "_runtime" / "current_session.json").exists()


# --- catch-up window and resumption floor ------------------------------------

def _fix_now(monkeypatch, iso):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat(iso)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(wfd._impl, "datetime", FixedDateTime)


def _complete_immediately(monkeypatch, seen):
    def complete(session, *_args):
        seen.append(session)
        return {
            "strict": True, "near": True, "probe_ok": True,
            "bars_ratio": 1.0, "master_ratio": 1.0,
            "bars_n": 100, "master_n": 100,
        }

    monkeypatch.setattr(wfd._impl, "evaluate", complete)
    monkeypatch.setattr(
        wfd._impl.time, "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )


def test_gap_older_than_the_window_is_abandoned_and_reported(tmp_path, monkeypatch, capsys):
    # 金曜07-10が最新公開のまま水曜07-15の夕方 → 当日だけを対象にし、07-13/14 は切り捨てて名指し。
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-07-10"]}), encoding="utf-8")
    _fix_now(monkeypatch, "2026-07-15T19:10:00+09:00")
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-07-14", 100, 100))
    seen = []
    _complete_immediately(monkeypatch, seen)
    monkeypatch.setattr(wfd._impl.sys, "argv", ["wait_for_data.py", "--manifest", str(manifest)])

    assert wfd.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "SESSION=2026-07-15"
    assert "WARN 切り捨て=2026-07-13, 2026-07-14" in captured.err
    assert "2件" in captured.err
    assert seen == ["2026-07-15"]


def test_saturday_run_catches_up_an_unpublished_friday(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-07-16"]}), encoding="utf-8")
    _fix_now(monkeypatch, "2026-07-18T16:40:00+09:00")
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-07-16", 100, 100))
    seen = []
    _complete_immediately(monkeypatch, seen)
    monkeypatch.setattr(wfd._impl.sys, "argv", ["wait_for_data.py", "--manifest", str(manifest)])

    assert wfd.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "SESSION=2026-07-17"
    assert "待機なし" in captured.err
    assert "切り捨て" not in captured.err
    assert seen == ["2026-07-17"]


def _holidays_2026_09(monkeypatch):
    closed = {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)}
    monkeypatch.setattr(
        wfd._impl.business_day, "is_business_day",
        lambda d: d.weekday() < 5 and d not in closed,
    )


def test_publication_floor_skips_everything_before_the_restart(tmp_path, monkeypatch, capsys):
    # 2026-09 の再開: 08-14 が最新公開のまま祝日09-21に発火 → 下限09-24より前は候補にせず SKIP。
    _holidays_2026_09(monkeypatch)
    monkeypatch.setattr(wfd._impl, "PUBLICATION_FLOOR", date(2026, 9, 24))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-08-14"]}), encoding="utf-8")
    _fix_now(monkeypatch, "2026-09-21T16:40:00+09:00")
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    monkeypatch.setattr(wfd._impl.sys, "argv", ["wait_for_data.py", "--manifest", str(manifest)])

    assert wfd.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "SKIP"
    assert "WARN 切り捨て=2026-08-17, 2026-08-18, 2026-08-19" in captured.err
    assert "（他15件）" in captured.err
    assert "下限2026-09-24" in captured.err


def test_first_session_on_or_after_the_floor_is_selected(tmp_path, monkeypatch, capsys):
    _holidays_2026_09(monkeypatch)
    monkeypatch.setattr(wfd._impl, "PUBLICATION_FLOOR", date(2026, 9, 24))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dates": ["2026-08-14"]}), encoding="utf-8")
    _fix_now(monkeypatch, "2026-09-24T16:40:00+09:00")
    monkeypatch.setenv("JQUANTS_API_KEY", "test")
    monkeypatch.setattr(wfd._impl, "_prev_counts", lambda _date: ("2026-09-18", 100, 100))
    seen = []
    _complete_immediately(monkeypatch, seen)
    monkeypatch.setattr(wfd._impl.sys, "argv", ["wait_for_data.py", "--manifest", str(manifest)])

    assert wfd.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "SESSION=2026-09-24"
    assert "切り捨て=2026-08-17" in captured.err
    assert seen == ["2026-09-24"]


def test_select_target_session_honours_the_window_size(monkeypatch):
    # 火曜07-21 夕方、木曜07-16 が最新公開。07-20 は祝日扱い（海の日）。
    monkeypatch.setattr(
        wfd._impl.business_day, "is_business_day",
        lambda d: d.weekday() < 5 and d != date(2026, 7, 20),
    )
    now = datetime.fromisoformat("2026-07-21T19:10:00+09:00")
    published = [date(2026, 7, 16)]

    assert wfd.select_target_session(now, published, window=1) == date(2026, 7, 21)
    assert wfd._impl.abandoned_sessions(now, published, window=1) == [date(2026, 7, 17)]
    assert wfd.select_target_session(now, published, window=2) == date(2026, 7, 17)
    assert wfd._impl.abandoned_sessions(now, published, window=2) == []
    assert wfd.select_target_session(now, [date(2026, 7, 21)], window=2) is None
    assert wfd._impl.abandoned_sessions(now, [], window=1) == []
    with pytest.raises(ValueError):
        wfd.select_target_session(now, published, window=0)
