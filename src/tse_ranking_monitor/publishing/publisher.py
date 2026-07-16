"""Build GitHub Pages artifacts and safely send ranking notifications."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..contracts import (
    FACTOR_KINDS,
    RANKING_SCHEMA_VERSION,
    validate_ranking_document,
)
from . import gmail, render

JST = timezone(timedelta(hours=9), name="JST")
RANKING_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
MANIFEST_SCHEMA_VERSION = 1
NOTIFY_ENV = (*gmail.REQUIRED_CREDENTIALS, "NOTIFY_TO")


class PublishError(RuntimeError):
    """A safe, user-facing publishing failure."""


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_bytes(path, payload):
    """Atomically replace *path* with *payload* in the same filesystem."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_text(path, text):
    _atomic_write_bytes(path, text.encode("utf-8"))


def _valid_session_date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_ranking(data):
    """Reject structurally incomplete ranking documents before publication."""
    if not isinstance(data, dict) or not _valid_session_date(data.get("session_date")):
        raise PublishError("invalid ranking json: session_date must be YYYY-MM-DD")
    try:
        return validate_ranking_document(data, require_factors=True)
    except ValueError as exc:
        raise PublishError(f"invalid ranking json: {exc}") from exc


def prepare_ranking(data):
    prepared = copy.deepcopy(data)
    if prepared.get("schema_version") is None:
        prepared["schema_version"] = RANKING_SCHEMA_VERSION
    validate_ranking(prepared)
    return prepared


def save_data(data, docs_dir):
    """Write the complete normalized ranking JSON and return its path."""
    prepared = prepare_ranking(data)
    path = Path(docs_dir) / "data" / f"{prepared['session_date']}.json"
    _atomic_write_bytes(path, _json_bytes(prepared))
    print(f"  daily json: {path} ({len(prepared.get('rows', []))} rows)")
    return str(path)


def cleanup_old(docs_dir, keep_days=30, today=None):
    data_dir = Path(docs_dir) / "data"
    if not data_dir.exists():
        return
    today = today or datetime.now(JST).date()
    cutoff = today - timedelta(days=keep_days)
    for path in data_dir.glob("*.json"):
        if path.name == "manifest.json":
            continue
        try:
            artifact_date = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if artifact_date < cutoff:
            path.unlink()


def _ranking_paths(data_dir):
    ranked = []
    for path in Path(data_dir).glob("*.json"):
        match = RANKING_FILE_RE.fullmatch(path.name)
        if not match:
            continue
        try:
            parsed = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        ranked.append((parsed, path))
    return sorted(ranked, reverse=True)


def update_manifest(docs_dir):
    """Write a backward-compatible date list plus per-ranking SHA-256 metadata."""
    data_dir = Path(docs_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rankings = _ranking_paths(data_dir)
    dates = [day.isoformat() for day, _ in rankings]
    artifacts = {}
    for day, path in rankings:
        artifacts[day.isoformat()] = {
            "ranking": {
                "path": path.name,
                "sha256": _sha256(path.read_bytes()),
            }
        }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dates": dates,
        "artifacts": artifacts,
    }
    _atomic_write_bytes(data_dir / "manifest.json", _json_bytes(manifest))
    return dates


def write_index(docs_dir):
    path = Path(docs_dir) / "index.html"
    _atomic_write_text(path, render.generate_pages_html())
    print(f"  pages html: {path}")
    return str(path)


def _required_notification_environment():
    missing = [name for name in NOTIFY_ENV if not os.environ.get(name)]
    if missing:
        raise PublishError("notification environment is incomplete: " + ", ".join(missing))


def send_email(data, html_body):
    """Send one email or raise; notification failures are never treated as success."""
    _required_notification_environment()
    rows = data.get("rows", [])
    counts = data.get("counts", {}) or {}
    sent = gmail.send_gmail(
        html_body,
        data["session_date"],
        len(rows),
        total=counts.get("qualifying", len(rows)),
        capped=bool(data.get("capped")),
    )
    if not sent:
        raise PublishError("Gmail API did not confirm the send")
    return True


def _fetch_bytes(url):
    req = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "tse-ranking-monitor-livecheck",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read()


def _manifest_digest(manifest, session):
    try:
        return manifest["artifacts"][session]["ranking"]["sha256"]
    except (KeyError, TypeError):
        return None


def wait_until_live(pages_url, session, expected_digest=None, timeout=300, interval=10):
    """Confirm both manifest and ranking bytes on Pages before email delivery."""
    if not pages_url or pages_url in ("./", "."):
        raise PublishError("pages-url is required for notification")
    if not _valid_session_date(session):
        raise PublishError("invalid session date for live check")
    if not expected_digest:
        raise PublishError("expected ranking digest is required for live check")

    base = pages_url.rstrip("/")
    deadline = time.monotonic() + max(0, timeout)
    checks = 0
    while True:
        checks += 1
        cache_buster = time.time_ns()
        try:
            manifest_bytes = _fetch_bytes(
                f"{base}/data/manifest.json?cb={cache_buster}"
            )
            manifest = json.loads(manifest_bytes)
            dates = manifest.get("dates", [])
            live_digest = _manifest_digest(manifest, session)
            if dates and dates[0] == session and live_digest == expected_digest:
                ranking_bytes = _fetch_bytes(
                    f"{base}/data/{session}.json?cb={cache_buster}"
                )
                if _sha256(ranking_bytes) == expected_digest:
                    print(
                        f"  live confirmed: Pages newest={session}, "
                        f"sha256={expected_digest[:12]}… (checks={checks})"
                    )
                    return True
            newest = dates[0] if dates else None
            print(
                f"  not live yet (newest={newest}, digest_match="
                f"{live_digest == expected_digest}); wait {interval}s"
            )
        except urllib.error.HTTPError as exc:
            print(f"  live-check HTTP {exc.code}; wait {interval}s")
        except Exception as exc:
            print(f"  live-check {type(exc).__name__}: {exc}; wait {interval}s")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(0, interval), remaining))
    raise PublishError(f"Pages not confirmed live within {timeout}s; email was not sent")


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"cannot read JSON {path}: {exc}") from exc


def _load_notification_artifact(input_path, docs_dir):
    """Bind notification to the exact locally published artifact."""
    requested = prepare_ranking(_load_json(input_path))
    session = requested["session_date"]
    published_path = Path(docs_dir) / "data" / f"{session}.json"
    if not published_path.is_file():
        raise PublishError(f"published ranking does not exist: {published_path}")

    published_bytes = published_path.read_bytes()
    published = prepare_ranking(_load_json(published_path))
    if _sha256(_json_bytes(requested)) != _sha256(_json_bytes(published)):
        raise PublishError("input ranking does not match the locally published ranking")

    expected_digest = _sha256(published_bytes)
    manifest_path = Path(docs_dir) / "data" / "manifest.json"
    manifest = _load_json(manifest_path)
    if _manifest_digest(manifest, session) != expected_digest:
        raise PublishError("local manifest digest does not match the published ranking")
    return published, expected_digest


def build(data, docs_dir):
    prepared = prepare_ranking(data)
    print(f"Publishing {prepared['session_date']} ({len(prepared.get('rows', []))} rows) ...")
    save_data(prepared, docs_dir)
    cleanup_old(docs_dir, keep_days=30)
    update_manifest(docs_dir)
    write_index(docs_dir)


def _verify_pushed_head(repo_root=None):
    """Require the local HEAD to be exactly the commit origin/main serves.

    Binding the notification to a successfully pushed HEAD makes the losing
    run of a push race (non-fast-forward reject) structurally unable to send
    an email for content Pages will never serve.  A third-party commit landing
    between push and notify also fails here — a safe unsent-nonzero exit
    instead of notifying for state that is no longer what main serves.
    """
    cwd = str(repo_root) if repo_root else None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, capture_output=True, text=True, check=True, timeout=60,
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "ls-remote", "origin", "main"],
            cwd=cwd, capture_output=True, text=True, check=True, timeout=60,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublishError(
            f"cannot verify pushed HEAD before notify: {exc}"
        ) from exc
    remote_head = remote[0] if remote else ""
    if not head or not remote_head:
        raise PublishError("cannot verify pushed HEAD before notify: empty git output")
    if head != remote_head:
        raise PublishError(
            "local HEAD %s is not what origin/main serves (%s); "
            "the push must succeed before the notification email"
            % (head[:12], remote_head[:12])
        )


def notify(input_path, docs_dir, pages_url, timeout=300, interval=10):
    _verify_pushed_head()
    data, expected_digest = _load_notification_artifact(input_path, docs_dir)
    print(
        f"Notify {data['session_date']} ({len(data.get('rows', []))} rows): "
        "wait for exact Pages artifact then send ..."
    )
    _required_notification_environment()
    wait_until_live(
        pages_url,
        data["session_date"],
        expected_digest=expected_digest,
        timeout=timeout,
        interval=interval,
    )
    email_html = render.generate_email_html(data, pages_url)
    send_email(data, email_html)


def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--in",
        dest="inp",
        required=True,
        help="build_day_ranking.py の出力 JSON（要因記入済み）",
    )
    parser.add_argument("--docs", default="docs", help="GitHub Pages の docs ディレクトリ")
    parser.add_argument("--pages-url", default=os.environ.get("PAGES_URL", "./"))
    parser.add_argument(
        "--notify",
        action="store_true",
        help="push 後、Pages 上の内容一致を確認して Gmail 送信（生成しない）",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="--notify の後方互換エイリアス（即時送信は行わない）",
    )
    parser.add_argument("--live-timeout", type=int, default=300)
    parser.add_argument("--live-interval", type=int, default=10)
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if args.notify or args.send:
            if args.send:
                print("  --send is a safe alias of --notify; immediate pre-push send is disabled.")
            notify(
                args.inp,
                args.docs,
                args.pages_url,
                timeout=args.live_timeout,
                interval=args.live_interval,
            )
        else:
            build(_load_json(args.inp), args.docs)
        return 0
    except (PublishError, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
