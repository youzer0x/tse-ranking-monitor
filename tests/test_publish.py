"""Publishing and safe-notification tests (all network calls are fakes)."""

import hashlib
import json
from pathlib import Path

import pytest

import publish as pub


def _ranking(session="2026-07-15", factor="材料を確認"):
    return {
        "session_date": session,
        "session_window": f"{session} 09:00–15:30 JST",
        "criteria": {
            "min_pct": 5,
            "min_turnover_yen": 10_000_000,
            "min_mcap_oku": 100,
            "max_rank": 30,
        },
        "counts": {"qualifying": 1, "ranked": 1},
        "capped": False,
        "rows": [{
            "rank": 1,
            "code": "7000",
            "name": "テスト銘柄",
            "pct": 8.5,
            "mcap_oku": 250,
            "factor": factor,
            "factor_kind": "報道",
        }],
    }


def _write_input(tmp_path, data=None):
    path = tmp_path / "ranking.json"
    path.write_text(json.dumps(data or _ranking(), ensure_ascii=False), encoding="utf-8")
    return path


def test_build_writes_versioned_digest_manifest_and_no_email_html(tmp_path):
    docs = tmp_path / "docs"
    pub.build(_ranking(), docs)

    ranking_path = docs / "data" / "2026-07-15.json"
    stored = json.loads(ranking_path.read_text(encoding="utf-8"))
    manifest = json.loads((docs / "data" / "manifest.json").read_text(encoding="utf-8"))

    assert stored["schema_version"] == 1
    assert manifest["schema_version"] == 1
    assert manifest["dates"] == ["2026-07-15"]
    assert manifest["artifacts"]["2026-07-15"]["ranking"]["sha256"] == (
        hashlib.sha256(ranking_path.read_bytes()).hexdigest()
    )
    assert (docs / "index.html").read_text(encoding="utf-8") == pub.render.generate_pages_html()
    assert not list((docs / "data").glob("*_email.html"))


def test_manifest_excludes_market_sidecar(tmp_path):
    docs = tmp_path / "docs"
    data_dir = docs / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "2026-07-15.json").write_text("{}", encoding="utf-8")
    (data_dir / "2026-07-15_market.json").write_text("{}", encoding="utf-8")

    assert pub.update_manifest(docs) == ["2026-07-15"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["rows"][0].update(factor=""), "factor is required"),
        (lambda d: d["rows"][0].update(factor_kind="確認不可"), "factor_kind"),
        (lambda d: d["counts"].update(ranked=2), "counts.ranked"),
        (lambda d: d.update(capped=True), "capped must reflect"),
        (lambda d: d["rows"].append(dict(d["rows"][0], rank=2)), "duplicate code"),
    ],
)
def test_invalid_ranking_is_not_published(tmp_path, mutate, message):
    data = _ranking()
    mutate(data)
    with pytest.raises(pub.PublishError, match=message):
        pub.save_data(data, tmp_path / "docs")
    assert not (tmp_path / "docs" / "data" / "2026-07-15.json").exists()


def test_wait_until_live_requires_exact_manifest_and_json_digest(monkeypatch):
    ranking_bytes = b'{"schema_version":1}'
    digest = hashlib.sha256(ranking_bytes).hexdigest()
    manifest = json.dumps({
        "dates": ["2026-07-15"],
        "artifacts": {"2026-07-15": {"ranking": {"sha256": digest}}},
    }).encode()
    responses = iter([manifest, ranking_bytes])
    monkeypatch.setattr(pub._implementation, "_fetch_bytes", lambda _url: next(responses))

    assert pub.wait_until_live(
        "https://example.github.io/site",
        "2026-07-15",
        expected_digest=digest,
        timeout=0,
        interval=0,
    )


def test_same_date_with_old_digest_times_out_without_fetching_ranking(monkeypatch):
    expected = hashlib.sha256(b"new").hexdigest()
    old = hashlib.sha256(b"old").hexdigest()
    manifest = json.dumps({
        "dates": ["2026-07-15"],
        "artifacts": {"2026-07-15": {"ranking": {"sha256": old}}},
    }).encode()
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return manifest

    monkeypatch.setattr(pub._implementation, "_fetch_bytes", fake_fetch)
    with pytest.raises(pub.PublishError, match="email was not sent"):
        pub.wait_until_live(
            "https://example.github.io/site",
            "2026-07-15",
            expected_digest=expected,
            timeout=0,
            interval=0,
        )
    assert len(calls) == 1


def test_notify_rejects_input_that_differs_from_published_json(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    published_input = _write_input(tmp_path, _ranking(factor="公開済み"))
    pub.build(_ranking(factor="公開済み"), docs)
    requested_input = tmp_path / "changed.json"
    requested_input.write_text(
        json.dumps(_ranking(factor="未公開の訂正"), ensure_ascii=False), encoding="utf-8"
    )
    sent = []
    monkeypatch.setattr(pub._implementation, "send_email", lambda *_: sent.append(True))

    with pytest.raises(pub.PublishError, match="does not match"):
        pub.notify(requested_input, docs, "https://example.github.io/site", timeout=0)
    assert not sent
    assert published_input.exists()


def test_missing_notification_environment_is_nonzero_and_unsent(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    input_path = _write_input(tmp_path)
    pub.build(_ranking(), docs)
    for name in pub.NOTIFY_ENV:
        monkeypatch.delenv(name, raising=False)
    live_calls = []
    monkeypatch.setattr(
        pub._implementation, "wait_until_live", lambda *_a, **_k: live_calls.append(True)
    )

    assert pub.main([
        "--in", str(input_path),
        "--docs", str(docs),
        "--pages-url", "https://example.github.io/site",
        "--notify",
    ]) == 1
    assert not live_calls


def test_live_timeout_is_nonzero_and_email_is_not_called(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    input_path = _write_input(tmp_path)
    pub.build(_ranking(), docs)
    sent = []
    monkeypatch.setattr(
        pub._implementation, "_required_notification_environment", lambda: None
    )
    monkeypatch.setattr(
        pub._implementation,
        "wait_until_live",
        lambda *_a, **_k: (_ for _ in ()).throw(pub.PublishError("timeout")),
    )
    monkeypatch.setattr(
        pub._implementation, "send_email", lambda *_a: sent.append(True)
    )

    assert pub.main([
        "--in", str(input_path),
        "--docs", str(docs),
        "--pages-url", "https://example.github.io/site",
        "--notify",
    ]) == 1
    assert not sent


def test_gmail_api_failure_is_nonzero(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    input_path = _write_input(tmp_path)
    pub.build(_ranking(), docs)
    monkeypatch.setattr(
        pub._implementation, "_required_notification_environment", lambda: None
    )
    monkeypatch.setattr(
        pub._implementation, "wait_until_live", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        pub._implementation,
        "send_email",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("Gmail API failed")),
    )

    assert pub.main([
        "--in", str(input_path),
        "--docs", str(docs),
        "--pages-url", "https://example.github.io/site",
        "--notify",
    ]) == 1


def test_send_flag_is_safe_notify_alias(tmp_path, monkeypatch):
    input_path = _write_input(tmp_path)
    calls = []
    monkeypatch.setattr(
        pub._implementation,
        "notify",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert pub.main([
        "--in", str(input_path),
        "--docs", str(tmp_path / "docs"),
        "--pages-url", "https://example.github.io/site",
        "--send",
    ]) == 0
    assert len(calls) == 1
