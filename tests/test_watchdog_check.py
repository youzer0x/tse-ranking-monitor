"""配信欠落watchdogの判定テスト（ネット無し・固定時刻。2026-07-15は水曜）。"""
import json
from datetime import date

import watchdog_check as wdc


def _manifest(tmp_path, dates, name="manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "schema_version": 1,
        "dates": dates,
        "artifacts": {
            d: {"ranking": {"path": f"{d}.json", "sha256": "0" * 64}} for d in dates
        },
    }), encoding="utf-8")
    return path


def test_ok_when_latest_session_is_published(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15", "2026-07-14"])

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-15T19:10:00+09:00",
    ]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_missing_when_completed_session_is_absent(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-15T19:10:00+09:00",
    ]) == 1
    assert capsys.readouterr().out.strip() == "MISSING=2026-07-15"


def test_gap_reports_oldest_unpublished_business_day(tmp_path, capsys):
    # 最新公開が金曜07-10のまま水曜夕方 → 欠落は最古の未公開営業日=月曜07-13。
    manifest = _manifest(tmp_path, ["2026-07-10"])

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-15T19:10:00+09:00",
    ]) == 1
    assert capsys.readouterr().out.strip() == "MISSING=2026-07-13"


def test_weekend_evening_with_friday_published_is_ok(tmp_path, capsys):
    # 土曜07-18の夕方: 完了済み最新セッションは金曜07-17で公開済み → 欠落なし。
    manifest = _manifest(tmp_path, ["2026-07-17"])

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-18T19:10:00+09:00",
    ]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_holiday_is_ok_via_business_day(tmp_path, monkeypatch, capsys):
    # 月曜07-20を祝日扱いにする（jpholidayの有無に依存させない）。
    monkeypatch.setattr(
        wdc.business_day, "is_business_day",
        lambda d: d.weekday() < 5 and d != date(2026, 7, 20),
    )
    manifest = _manifest(tmp_path, ["2026-07-17"])

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-20T19:10:00+09:00",
    ]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_utc_now_is_equivalent_to_jst_now(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])

    # 10:10 UTC == 19:10 JST。JST表記と同じ判定になる。
    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-15T10:10:00+00:00",
    ]) == 1
    assert capsys.readouterr().out.strip() == "MISSING=2026-07-15"


def test_stale_live_manifest_is_reported(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15", "2026-07-14"])
    live = _manifest(tmp_path, ["2026-07-14"], name="live-manifest.json")

    assert wdc.main([
        "--manifest", str(manifest), "--live-manifest", str(live),
        "--now", "2026-07-15T19:10:00+09:00",
    ]) == 1
    assert capsys.readouterr().out.strip() == "PAGES_STALE=2026-07-15"


def test_current_live_manifest_is_ok(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15"])
    live = _manifest(tmp_path, ["2026-07-15"], name="live-manifest.json")

    assert wdc.main([
        "--manifest", str(manifest), "--live-manifest", str(live),
        "--now", "2026-07-15T19:10:00+09:00",
    ]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_malformed_manifest_is_exit_2_without_token(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("not json", encoding="utf-8")

    assert wdc.main([
        "--manifest", str(manifest), "--now", "2026-07-15T19:10:00+09:00",
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest" in captured.err


def test_missing_manifest_is_exit_2(tmp_path, capsys):
    assert wdc.main([
        "--manifest", str(tmp_path / "absent.json"),
        "--now", "2026-07-15T19:10:00+09:00",
    ]) == 2
    assert capsys.readouterr().out == ""


def test_unreadable_live_manifest_is_exit_2(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15"])
    live = tmp_path / "live-manifest.json"
    live.write_text("{]", encoding="utf-8")

    assert wdc.main([
        "--manifest", str(manifest), "--live-manifest", str(live),
        "--now", "2026-07-15T19:10:00+09:00",
    ]) == 2
    assert capsys.readouterr().out == ""


def test_bad_now_is_exit_2(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15"])

    assert wdc.main(["--manifest", str(manifest), "--now", "yesterday"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--now" in captured.err
