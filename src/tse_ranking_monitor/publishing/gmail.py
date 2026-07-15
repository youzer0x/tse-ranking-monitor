"""Send ranking notifications through the Gmail HTTPS API."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
REQUIRED_CREDENTIALS = (
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "GMAIL_ADDRESS",
)


def _urlopen_json(req, what):
    """Open an HTTPS request and return its JSON response with useful errors."""
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"{what} failed: HTTP {exc.code} {exc.reason}\n{body}"
        ) from None


def _require_credentials():
    missing = [name for name in REQUIRED_CREDENTIALS if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing Gmail credentials: " + ", ".join(missing))


def _access_token():
    """Exchange the configured refresh token for a short-lived access token."""
    _require_credentials()
    data = urllib.parse.urlencode({
        "client_id": os.environ["GMAIL_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    response = _urlopen_json(req, "Gmail token")
    token = response.get("access_token")
    if not token:
        raise RuntimeError("Gmail token response did not contain access_token")
    return token


def _subject(session_date, count, total=None, capped=False):
    if capped and total is not None:
        return f"[東証値上がり率ランキング] {session_date}｜{total}社該当・上位{count}社"
    return f"[東証値上がり率ランキング] {session_date}｜{count}社該当"


def _build_raw(sender, recipient, subject, session_date, count, total, capped, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    count_text = (
        f"{total}社該当（上位{count}社を掲載）"
        if capped and total is not None
        else f"{count}社該当"
    )
    msg.attach(MIMEText(
        f"{session_date} の東証 値上がり率ランキング"
        f"（{count_text}）です。HTML 表示に対応したメーラーでご覧ください。",
        "plain",
        "utf-8",
    ))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def send_gmail(html_body, session_date, count, total=None, capped=False):
    """Send one HTML notification; failures are raised to the caller."""
    _require_credentials()
    sender = os.environ["GMAIL_ADDRESS"]
    recipient = os.environ.get("NOTIFY_TO", sender)
    subject = _subject(session_date, count, total=total, capped=capped)

    print(f"  Sending email to {recipient} via Gmail API ...")
    token = _access_token()
    payload = json.dumps({
        "raw": _build_raw(
            sender, recipient, subject, session_date, count, total, capped, html_body
        )
    }).encode()
    req = urllib.request.Request(
        SEND_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    response = _urlopen_json(req, "Gmail send")
    message_id = response.get("id")
    if not message_id:
        raise RuntimeError("Gmail send response did not contain a message id")
    print(f"  Email sent. id={message_id}")
    return True
