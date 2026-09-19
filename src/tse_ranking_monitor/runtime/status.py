"""Durable run status for unattended routine sessions.

Telemetry lives under the gitignored ``.work/`` and the only way it ever left the
machine was inside a failure email that is not sent when the session dies.  A
killed process cannot report on itself, so the position it reached has to be
pushed somewhere an external observer can read *while the run is still alive*.

The status is committed to a dedicated branch with git plumbing only.  The
routine's working tree carries uncommitted publication artifacts at the moment
these writes happen, so nothing here may touch the index, HEAD, or the worktree.

Every failure is soft: losing the status costs the watchdog its extra detail, it
must never cost the delivery.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    TelemetryWriter,
    read_session_pointer,
    utc_now,
)

STATUS_BRANCH = "routine-status"
STATUS_DIR = "status"
LATEST_PATH = "%s/latest.json" % STATUS_DIR


class StatusError(RuntimeError):
    """A status publication failed.  Callers downgrade this to a warning."""


def elog(message):
    print("[run-status] %s" % message, file=sys.stderr, flush=True)


def _tail(text, limit=200):
    """The part of git's stderr worth putting in an error message."""
    return " ".join((text or "").split())[:limit]


def _git_result(repo_root, args, *, env=None, check=True):
    """Run git and return the ``CompletedProcess``.

    With ``check`` a non-zero exit raises :class:`StatusError` carrying git's
    stderr -- ``CalledProcessError`` alone hides it, which is how the fetch-less
    ``read-tree`` failure went unnoticed in the cloud for weeks.
    """
    merged = dict(os.environ)
    if env:
        merged.update(env)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env=merged,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StatusError("git %s failed: %s" % (" ".join(args), exc)) from exc
    if check and completed.returncode != 0:
        raise StatusError("git %s failed (exit %d): %s"
                          % (" ".join(args), completed.returncode, _tail(completed.stderr)))
    return completed


def _git(repo_root, args, *, env=None, check=True):
    return _git_result(repo_root, args, env=env, check=check).stdout


def status_path_for(session):
    return "%s/%s.json" % (STATUS_DIR, session)


def build_status(session, *, delivered=False, last_stage=None, died_at=None,
                 run_id=None, started_at=None, stages=None, note=None):
    """Build the status document.  Pure -- no clock beyond ``utc_now``."""
    status = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "session": str(session),
        "updated_at": utc_now(),
        "delivered": bool(delivered),
    }
    if started_at:
        status["started_at"] = started_at
    if run_id:
        status["run_id"] = str(run_id)[:128]
    if last_stage:
        status["last_stage"] = str(last_stage)[:128]
    if died_at:
        status["died_at"] = str(died_at)[:128]
    if stages:
        status["stages"] = [str(stage)[:128] for stage in stages]
    if note:
        status["note"] = str(note)[:500]
    return status


def collect_status(root, session, *, delivered=False, died_at=None, note=None):
    """Assemble the status for ``session`` from on-disk telemetry."""
    writer = TelemetryWriter(root)
    unfinished = [stage for stage, _started in writer.unfinished_stages(session)]
    pointer = read_session_pointer(root) or {}
    return build_status(
        session,
        delivered=delivered,
        last_stage=unfinished[0] if unfinished else None,
        died_at=died_at,
        run_id=pointer.get("run_id"),
        started_at=pointer.get("started_at"),
        stages=unfinished,
        note=note,
    )


def _resolve_tip(repo_root, remote, branch):
    """Fetch the status branch and return its tip sha, or ``None`` when it is new.

    Knowing the tip is not enough: ``read-tree`` and ``commit-tree -p`` need the
    tip's objects locally, and the routine runs in a fresh clone of ``main``
    that has none of them.  The fetch lands in a remote-tracking ref rather
    than ``FETCH_HEAD`` so a concurrent fetch by the routine in the same repo
    cannot clobber it between the two calls.
    """
    tracking = "refs/remotes/%s/%s" % (remote, branch)
    fetched = _git_result(
        repo_root,
        ["fetch", "--no-tags", remote, "+refs/heads/%s:%s" % (branch, tracking)],
        check=False,
    )
    if fetched.returncode == 0:
        return _git(repo_root, ["rev-parse", "--verify", "%s^{commit}" % tracking]).strip()
    # Tell "the branch does not exist yet" apart from a real failure.  ls-remote
    # raises on a dead remote on purpose: treating that as "new branch" would
    # mint an orphan commit that the push then rejects.
    listing = _git(repo_root, ["ls-remote", remote, "refs/heads/%s" % branch])
    if not listing.strip():
        return None
    raise StatusError("git fetch %s %s failed (exit %d): %s"
                      % (remote, branch, fetched.returncode, _tail(fetched.stderr)))


def _write_blob(repo_root, text):
    """Store ``text`` as a loose blob and return its sha."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                     suffix=".json", delete=False) as handle:
        handle.write(text)
        temp_name = handle.name
    try:
        sha = _git(repo_root, ["hash-object", "-w", "--", temp_name]).strip()
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
    if len(sha) != 40:
        raise StatusError("unexpected blob id %r" % sha)
    return sha


def _commit_status(repo_root, status, blob, tip):
    """Build a commit carrying ``blob`` at both status paths.  Returns its sha."""
    session = status["session"]
    index = Path(tempfile.gettempdir()) / (".tse-status-index-%d" % os.getpid())
    env = {
        "GIT_INDEX_FILE": str(index),
        # commit-tree refuses to run without an identity, and the cloud routine's
        # git config is not guaranteed to be readable from this process.
        "GIT_AUTHOR_NAME": "tse-ranking-monitor",
        "GIT_AUTHOR_EMAIL": "routine@localhost",
        "GIT_COMMITTER_NAME": "tse-ranking-monitor",
        "GIT_COMMITTER_EMAIL": "routine@localhost",
    }
    try:
        if tip:
            _git(repo_root, ["read-tree", tip], env=env)
        else:
            _git(repo_root, ["read-tree", "--empty"], env=env)
        for path in (status_path_for(session), LATEST_PATH):
            _git(repo_root,
                 ["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, path)],
                 env=env)
        tree = _git(repo_root, ["write-tree"], env=env).strip()
        args = ["commit-tree", tree]
        if tip:
            args += ["-p", tip]
        args += ["-m", "run status %s" % session]
        return _git(repo_root, args, env=env).strip()
    finally:
        try:
            index.unlink()
        except OSError:
            pass


def publish_status(root, status, *, remote="origin", branch=STATUS_BRANCH, attempts=2):
    """Commit ``status`` to ``branch`` without touching the worktree, index, or HEAD.

    Uses a throwaway index via ``GIT_INDEX_FILE`` so the routine's staged
    publication artifacts stay exactly as they were.  Never force-pushes: on a
    lost race it re-reads the tip and retries, then gives up.

    Raises :class:`StatusError`; callers downgrade it to a warning.
    """
    repo_root = Path(root)
    session = status.get("session")
    if not session:
        raise StatusError("status document has no session")
    blob = _write_blob(repo_root, json.dumps(status, ensure_ascii=False, indent=2) + "\n")

    last_error = None
    for _attempt in range(max(1, attempts)):
        tip = _resolve_tip(repo_root, remote, branch)
        commit = _commit_status(repo_root, status, blob, tip)
        if len(commit) != 40:
            raise StatusError("unexpected commit id %r" % commit)
        try:
            _git(repo_root, ["push", remote, "%s:refs/heads/%s" % (commit, branch)])
            return commit
        except StatusError as exc:
            last_error = exc
    raise StatusError("could not push run status: %s" % last_error)


def publish_status_quietly(root, status, **kwargs):
    """``publish_status`` that reports failures instead of raising.

    Status publication is observability: a branch-push restriction or a network
    blip must not interrupt the delivery it is describing.
    """
    try:
        return publish_status(root, status, **kwargs)
    except StatusError as exc:
        elog("WARN ステータスの公開に失敗（配信は継続）: %s" % exc)
        return None
