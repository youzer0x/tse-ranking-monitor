"""Layer B 失敗アラートのテスト（Gmailはfake・時計は固定・ネット無し）。"""
import json
from datetime import datetime, timedelta, timezone

import notify_failure as nf

JST = timezone(timedelta(hours=9))


def _write_events(tmp_path, session="2026-07-15"):
    telemetry_dir = tmp_path / session / "telemetry"
    telemetry_dir.mkdir(parents=True)
    (telemetry_dir / "events.jsonl").write_text(
        "\n".join([
            json.dumps({"event": "subagent_start", "session_date": session}),
            '{"torn append',   # 壊れた行は黙って読み飛ばす
            json.dumps({"event": "tool_end", "status": "failed", "session_date": session}),
        ]) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _write_repair_targets(tmp_path):
    path = tmp_path / "ranking_targets.json"
    path.write_text(json.dumps({
        "schema_version": "quality_findings.v1",
        "validator": "ranking",
        "files": [{
            "file": "docs/data/2026-07-15.json",
            "targets": [{
                "code": "7013", "path": "$.rows[0]",
                "rule_ids": ["RANK_UNSOURCED_CAUSAL"], "severities": ["WARN"],
            }],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_build_failure_report_contains_all_context():
    telemetry = {
        "events": 12,
        "subagents": {"started": 3, "completed": 2},
        "tools": {"completed": 8, "failed": 1},
        "failures": 2,
    }
    subject, body = nf.build_failure_report(
        "2026-07-15", "stage2", "research timeout",
        telemetry=telemetry,
        residuals=["7013: RANK_UNSOURCED_CAUSAL(WARN)"],
        now_jst=datetime(2026, 7, 15, 18, 30, tzinfo=JST),
    )

    assert subject == "[tse-ranking-monitor] 配信失敗 2026-07-15｜stage2"
    assert "stage: stage2" in body
    assert "reason: research timeout" in body
    assert "events=12" in body and "subagents=2/3" in body and "failures=2" in body
    assert "7013: RANK_UNSOURCED_CAUSAL(WARN)" in body
    assert "2026-07-15 18:30 JST" in body


def test_main_sends_report_with_telemetry_and_residuals(tmp_path, monkeypatch):
    work = _write_events(tmp_path / "work")
    repair = _write_repair_targets(tmp_path)
    sent = []
    monkeypatch.setattr(
        nf._implementation.gmail, "send_plain_email",
        lambda subject, body, recipient=None: sent.append((subject, body)) or True,
    )

    assert nf.main([
        "--stage", "publish", "--reason", "push rejected",
        "--session", "2026-07-15",
        "--work-dir", str(work),
        "--repair-targets", str(repair),
    ]) == 0

    subject, body = sent[0]
    assert "配信失敗 2026-07-15｜publish" in subject
    assert "reason: push rejected" in body
    assert "events=2" in body and "tools_failed=1" in body   # 壊れた行を除いた2件
    assert "7013: RANK_UNSOURCED_CAUSAL(WARN)" in body


def test_main_returns_one_and_never_raises_when_sender_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        nf._implementation.gmail, "send_plain_email",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("Gmail API down")),
    )

    assert nf.main(["--stage", "publish", "--reason", "x"]) == 1
    assert "Gmail API down" in capsys.readouterr().err


def test_main_tolerates_missing_work_dir_and_session(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        nf._implementation.gmail, "send_plain_email",
        lambda subject, body, recipient=None: sent.append((subject, body)) or True,
    )

    assert nf.main([
        "--stage", "gate", "--reason", "TIMEOUT",
        "--work-dir", str(tmp_path / "no-such-dir"),
    ]) == 0

    subject, body = sent[0]
    assert "unknown" in subject
    assert "telemetry:" not in body and "quality residuals:" not in body
