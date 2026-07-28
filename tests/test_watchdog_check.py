"""配信欠落watchdogの判定テスト（ネット無し・固定時刻。2026-07-15は水曜）。"""
import json
from datetime import date
from pathlib import Path

import watchdog_check as wdc

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "watchdog.yml"


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


def _status(tmp_path, payload, name="status.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_started_but_undelivered_session_reports_the_stage(tmp_path, capsys):
    """MISSING cannot distinguish "never fired" from "died part-way"."""
    manifest = _manifest(tmp_path, ["2026-07-14"])
    status = _status(tmp_path, {
        "session": "2026-07-15", "delivered": False, "died_at": "stage2",
    })

    code = wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"])

    assert code == 1
    assert capsys.readouterr().out.strip() == "STALLED=2026-07-15:stage=stage2"


def test_stall_falls_back_to_last_stage_when_no_death_recorded(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])
    status = _status(tmp_path, {
        "session": "2026-07-15", "delivered": False, "last_stage": "research",
    })

    assert wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"]) == 1
    assert capsys.readouterr().out.strip() == "STALLED=2026-07-15:stage=research"


def test_status_for_another_session_degrades_to_missing(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])
    status = _status(tmp_path, {
        "session": "2026-07-14", "delivered": False, "died_at": "stage2",
    })

    assert wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"]) == 1
    assert capsys.readouterr().out.strip() == "MISSING=2026-07-15"


def test_delivered_status_with_an_unpublished_manifest_stays_missing(tmp_path, capsys):
    """A run claiming success while the manifest disagrees is a publication
    problem, not a stall."""
    manifest = _manifest(tmp_path, ["2026-07-14"])
    status = _status(tmp_path, {"session": "2026-07-15", "delivered": True})

    assert wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"]) == 1
    assert capsys.readouterr().out.strip() == "MISSING=2026-07-15"


def test_unreadable_status_degrades_to_missing(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    assert wdc.main(["--manifest", str(manifest), "--status", str(broken),
                     "--now", "2026-07-15T19:10:00+09:00"]) == 1
    out = capsys.readouterr()
    assert out.out.strip() == "MISSING=2026-07-15"
    assert "実行ステータスを読めない" in out.err


def test_status_is_ignored_when_nothing_is_missing(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15"])
    status = _status(tmp_path, {
        "session": "2026-07-15", "delivered": False, "died_at": "stage2",
    })

    assert wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_stage_name_is_sanitised_to_keep_one_stdout_token(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-14"])
    status = _status(tmp_path, {
        "session": "2026-07-15", "delivered": False,
        "died_at": "stage 2\nrm -rf /",
    })

    assert wdc.main(["--manifest", str(manifest), "--status", status,
                     "--now", "2026-07-15T19:10:00+09:00"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.startswith("STALLED=2026-07-15:stage=")
    assert len(out.splitlines()) == 1
    assert " " not in out


def test_bad_now_is_exit_2(tmp_path, capsys):
    manifest = _manifest(tmp_path, ["2026-07-15"])

    assert wdc.main(["--manifest", str(manifest), "--now", "yesterday"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--now" in captured.err


# --- workflow wiring: the checker is only useful if the workflow feeds it ------

def test_workflow_feeds_the_durable_status_to_the_checker():
    """Without --status the checker can never distinguish a stall from a no-show."""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "origin routine-status" in text
    assert "status/latest.json" in text
    assert "--status /tmp/run-status.json" in text


def test_workflow_treats_stalled_as_a_problem():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "STALLED=*)" in text


def test_workflow_emits_annotations_so_detail_reaches_the_failure_mail():
    """Issue notifications for a bot-authored issue are not delivered; the
    "Run failed" mail is the real alarm and it includes annotations."""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "::error title=Delivery watchdog::" in text


def test_watchdog_only_runs_on_tse_weekdays():
    """Scoped to ranking target days: no weekend runs.  Holiday suppression is
    the checker's job via business_day, which cron cannot express."""
    text = WORKFLOW.read_text(encoding="utf-8")

    crons = [line.strip() for line in text.splitlines() if line.strip().startswith("- cron:")]
    assert crons, "watchdog must stay scheduled"
    for cron in crons:
        assert "* * 1-5" in cron, cron
