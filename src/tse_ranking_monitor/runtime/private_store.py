"""Private audit archives and a transactional, restart-safe delivery ledger.

TSE_PRIVATE_STATE_DIR may point at a persistent private volume. The default
stays gitignored; ephemeral runners must export/restore it between runs.
"""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import zipfile


def private_root(repo_root):
    repo_root = Path(repo_root).resolve()
    root = Path(os.environ.get("TSE_PRIVATE_STATE_DIR", repo_root / ".work" / "private")).resolve()
    for forbidden in (repo_root / "docs", repo_root / ".git"):
        if root == forbidden or forbidden in root.parents:
            raise ValueError("private storage must be outside docs and .git")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _connect(repo_root):
    db = sqlite3.connect(private_root(repo_root) / "delivery.sqlite3", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, session TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL, reason TEXT)")
    db.commit()
    return db


def delivery_key(session, digest, recipient):
    recipients = sorted({item.strip().casefold() for item in recipient.split(",") if item.strip()})
    return hashlib.sha256(json.dumps([session, digest, recipients], ensure_ascii=False).encode()).hexdigest()


def reserve_delivery(repo_root, session, digest, recipient):
    key = delivery_key(session, digest, recipient)
    with closing(_connect(repo_root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT status FROM deliveries WHERE key=?", (key,)).fetchone()
        if existing and existing[0] == "sent":
            return key, False
        if existing and existing[0] == "pending":
            raise ValueError(f"delivery {key} is pending/uncertain; inspect Gmail and resolve its status before retrying")
        db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,?,?,?,?,?)",
                   (key, session, digest, "pending", datetime.now(timezone.utc).isoformat(), None))
    return key, True


def finish_delivery(repo_root, key):
    with closing(_connect(repo_root)) as db, db:
        cursor = db.execute("UPDATE deliveries SET status='sent', updated=? WHERE key=? AND status='pending'",
                            (datetime.now(timezone.utc).isoformat(), key))
        if cursor.rowcount != 1:
            raise ValueError("delivery reservation is missing")


def resolve_delivery(repo_root, key, status, reason):
    if status not in {"sent", "not-sent"} or not reason.strip():
        raise ValueError("resolution requires sent/not-sent and an audit reason")
    with closing(_connect(repo_root)) as db, db:
        cursor = db.execute("UPDATE deliveries SET status=?, reason=?, updated=? WHERE key=?",
                            (status, reason, datetime.now(timezone.utc).isoformat(), key))
        if cursor.rowcount != 1:
            raise ValueError("unknown delivery key")


def archive_session(repo_root, session_dir, *, report=None):
    """Archive an explicit allowlist; credentials and raw tool logs are excluded."""
    session_dir = Path(session_dir).resolve()
    session = session_dir.name
    if date.fromisoformat(session).isoformat() != session:
        raise ValueError("archive directory must be named YYYY-MM-DD")
    files = []
    for pattern in ("stage1.json", "ranking.json", "checkpoint.json", "quality*.json", "research/manifest.json",
                    "research/evidence.json", "research/factors.json", "research/batches/*.json",
                    "research/results/*.json", "research/repair_targets.json", "market/*.json", "market/*.csv"):
        for path in session_dir.glob(pattern):
            if path.is_file() and not path.is_symlink() and session_dir in path.resolve().parents:
                files.append(path)
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                                  capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    payloads = {path.relative_to(session_dir).as_posix(): path.read_bytes() for path in sorted(set(files))}
    metadata = {"schema_version": 1, "session": session, "revision": revision,
                "created_at": datetime.now(timezone.utc).isoformat(), "report": report or {},
                "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}}
    directory = private_root(repo_root) / "archives" / session
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".zip")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
        archive.writestr("archive.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    return target


def export_private_state(repo_root, destination):
    source, target = private_root(repo_root), Path(destination).resolve()
    for forbidden in (source, Path(repo_root).resolve() / "docs", Path(repo_root).resolve() / ".git"):
        if target == forbidden or forbidden in target.parents:
            raise ValueError("export destination must be separate from private storage, docs and .git")
    if target.exists():
        raise ValueError("export destination already exists")
    target.mkdir(parents=True)
    with closing(_connect(repo_root)) as db, closing(sqlite3.connect(target / "delivery.sqlite3")) as backup:
        db.backup(backup)
    archives = source / "archives"
    if archives.exists():
        shutil.copytree(archives, target / "archives")
    return target


def expired_archives(repo_root, *, keep_days=180, today=None):
    if keep_days < 1:
        raise ValueError("keep-days must be positive")
    cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=keep_days)
    root = private_root(repo_root) / "archives"
    result = []
    for directory in root.glob("????-??-??"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            expired = date.fromisoformat(directory.name) < cutoff
        except ValueError:
            continue
        if expired:
            for path in directory.glob("*.zip"):
                if not path.is_symlink() and path.is_file() and root.resolve() in path.resolve().parents:
                    result.append(path)
    return sorted(result)
