"""Gmail API adapter tests with no network access."""

import base64
import json
from email import message_from_bytes

import pytest

import gmail_sender as gs


def _credentials(monkeypatch):
    values = {
        "GMAIL_CLIENT_ID": "client",
        "GMAIL_CLIENT_SECRET": "secret",
        "GMAIL_REFRESH_TOKEN": "refresh",
        "GMAIL_ADDRESS": "sender@example.com",
        "NOTIFY_TO": "reader@example.com",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_send_gmail_rejects_missing_credentials(monkeypatch):
    for name in gs.REQUIRED_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="missing Gmail credentials"):
        gs.send_gmail("<b>x</b>", "2026-07-15", 1)


def test_send_gmail_uses_token_then_send_api(monkeypatch):
    _credentials(monkeypatch)
    requests = []
    responses = iter([{"access_token": "token"}, {"id": "message-1"}])

    def fake_urlopen_json(request, what):
        requests.append((request, what))
        return next(responses)

    monkeypatch.setattr(gs._implementation, "_urlopen_json", fake_urlopen_json)
    assert gs.send_gmail(
        "<b>本文</b>", "2026-07-15", 30, total=42, capped=True
    )

    assert [what for _, what in requests] == ["Gmail token", "Gmail send"]
    send_request = requests[1][0]
    assert send_request.headers["Authorization"] == "Bearer token"
    raw = json.loads(send_request.data)["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    plain = message.get_payload(0).get_payload(decode=True).decode("utf-8")
    assert message["To"] == "reader@example.com"
    assert "42" in plain and "30" in plain


def test_send_gmail_requires_message_id(monkeypatch):
    _credentials(monkeypatch)
    responses = iter([{"access_token": "token"}, {}])
    monkeypatch.setattr(
        gs._implementation, "_urlopen_json", lambda *_args: next(responses)
    )
    with pytest.raises(RuntimeError, match="message id"):
        gs.send_gmail("<b>x</b>", "2026-07-15", 1)
