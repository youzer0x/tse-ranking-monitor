"""鮮度ゲートのbars必須判定と障害時フェイルセーフ。"""
import json
from datetime import datetime

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
