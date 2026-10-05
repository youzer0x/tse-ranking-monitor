import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import zipfile

import pytest

from publication_fixtures import research_bundle, market_document
from test_publish import _ranking, _write_input
from tse_ranking_monitor.publishing import publisher as pub
from tse_ranking_monitor.publishing.validation import require_market_quality
from tse_ranking_monitor.runtime import private_store as store
from tse_ranking_monitor.text import inline_html, session_url


def test_publication_requires_recompiled_evidence(tmp_path):
    docs = tmp_path / "docs"
    with pytest.raises(pub.PublishError, match="research-dir"):
        pub.build(_ranking(), docs)
    assert not docs.exists()
    ranking = _ranking()
    research = research_bundle(ranking, tmp_path / "research")
    ranking["rows"][0]["factor"] = "改変された説明"
    with pytest.raises(pub.PublishError, match="factors do not match"):
        pub.build(ranking, docs, research_dir=research)
    ranking["rows"][0]["factor"] = "材料を確認"
    ranking["rows"][0]["close"] = 2000
    with pytest.raises(pub.PublishError, match="Stage1 input"):
        pub.build(ranking, docs, research_dir=research)
    assert not docs.exists()


def test_quality_warning_blocks_even_with_matching_evidence(tmp_path):
    ranking = _ranking(factor="米ミクロンの上昇と並走したとみられる。")
    research = research_bundle(ranking, tmp_path / "research")
    with pytest.raises(pub.PublishError, match="RANK_FACTOR_NOTATION"):
        pub.build(ranking, tmp_path / "docs", research_dir=research)


def test_failed_market_is_quarantined_and_ranking_published(tmp_path):
    ranking = _ranking()
    research = research_bundle(ranking, tmp_path / "research")
    target = tmp_path / "docs/data/2026-07-15_market.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"bad":"draft"}', encoding="utf-8")
    report = pub.build(ranking, tmp_path / "docs", research_dir=research)
    assert report["market_failure"]
    assert not target.exists()
    assert (target.parent / "2026-07-15.json").exists()
    assert next((tmp_path / ".work/2026-07-15/quarantine").glob("*.json")).read_text() == '{"bad":"draft"}'


def test_market_numeric_contract_rejects_invalid_counts():
    market = market_document("2026-07-15")
    require_market_quality(market, "2026-07-15")
    market["market"]["breadth"]["up"] = "1"
    with pytest.raises(ValueError, match="numeric"):
        require_market_quality(market, "2026-07-15")


def test_repeated_notification_sends_once(tmp_path, monkeypatch):
    ranking = _ranking()
    docs = tmp_path / "docs"
    pub.build(ranking, docs, research_dir=research_bundle(ranking, tmp_path / "research"))
    path = _write_input(tmp_path, ranking)
    sent = []
    monkeypatch.setattr(pub, "_verify_pushed_head", lambda **_: None)
    monkeypatch.setattr(pub, "wait_until_live", lambda *_, **__: True)
    monkeypatch.setattr(pub, "_required_notification_environment", lambda: None)
    monkeypatch.setattr(pub, "mark_delivered", lambda *_: None)
    monkeypatch.setattr(pub, "send_email", lambda *args: sent.append(args))
    monkeypatch.setenv("NOTIFY_TO", "reader@example.com")
    pub.notify(path, docs, "https://example.com/")
    pub.notify(path, docs, "https://example.com/")
    assert len(sent) == 1


def test_simultaneous_delivery_reservation_and_uncertain_recovery(tmp_path):
    def reserve(_):
        try:
            return store.reserve_delivery(tmp_path, "2026-07-15", "abc", "a@example.com")
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(item is not None for item in results) == 1
    key = next(item[0] for item in results if item)
    store.resolve_delivery(tmp_path, key, "not-sent", "Gmailで未送信を確認")
    assert store.reserve_delivery(tmp_path, "2026-07-15", "abc", "a@example.com")[1]
    store.finish_delivery(tmp_path, key)
    assert not store.reserve_delivery(tmp_path, "2026-07-15", "abc", "A@example.com")[1]


def test_archive_allowlist_export_and_retention(tmp_path, monkeypatch):
    monkeypatch.delenv("TSE_PRIVATE_STATE_DIR", raising=False)
    session = tmp_path / ".work/2026-07-15"
    session.mkdir(parents=True)
    (session / "ranking.json").write_text("{}")
    (session / ".env").write_text("SECRET=hidden")
    (session / "raw.log").write_text("secret")
    archive = store.archive_session(tmp_path, session)
    with zipfile.ZipFile(archive) as content:
        assert set(content.namelist()) == {"ranking.json", "archive.json"}
        assert "revision" in json.loads(content.read("archive.json"))
    key, _ = store.reserve_delivery(tmp_path, session.name, "digest", "a@example.com")
    store.finish_delivery(tmp_path, key)
    exported = store.export_private_state(tmp_path, tmp_path / "backup")
    monkeypatch.setenv("TSE_PRIVATE_STATE_DIR", str(exported))
    assert not store.reserve_delivery(tmp_path, session.name, "digest", "a@example.com")[1]
    assert len(store.expired_archives(tmp_path, keep_days=180, today=date(2027, 7, 15))) == 1
    assert (exported / "delivery.sqlite3").exists()


def test_email_escapes_text_attributes_and_points_to_session():
    ranking = _ranking(factor='[記事](https://example.com/" onmouseover="alert(1)) <img src=x>')
    ranking["rows"][0]["name"] = '<img src=x onerror="alert(1)">'
    html = pub.render.generate_email_html(ranking, "https://example.com/?a=1#market")
    assert '<img src=x' not in html
    assert '&lt;img' in html
    assert 'date=2026-07-15#market' in html
    assert '<a href="https://example.com/&quot;' not in html
    assert '<a ' not in inline_html('[bad](javascript:alert(1))')
    assert session_url("https://example.com/?date=old#market", "2026-07-15") == "https://example.com/?date=2026-07-15#market"
