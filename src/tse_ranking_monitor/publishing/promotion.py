"""Validate a Claude routine publication before promoting it to ``main``.

The Claude cloud harness normally confines a session to its generated
``claude/*`` branch.  A GitHub Actions fallback may fast-forward ``main`` to
that branch, but only after this module proves that the candidate is the
single, docs-only daily publication commit expected by the routine.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import publisher


CLAUDE_BRANCH_RE = re.compile(r"^claude/[A-Za-z0-9._/-]+$")
RANKING_PATH_RE = re.compile(r"^docs/data/(\d{4}-\d{2}-\d{2})\.json$")
MARKET_PATH_RE = re.compile(r"^docs/data/(\d{4}-\d{2}-\d{2})_market\.json$")
MANIFEST_PATH = "docs/data/manifest.json"
INDEX_PATH = "docs/index.html"


class PromotionError(RuntimeError):
    """A fail-closed reason why a routine commit must not reach ``main``."""


@dataclass(frozen=True)
class PromotionCandidate:
    branch: str
    base_sha: str
    head_sha: str
    parent_sha: str
    session: str
    status: str
    changed_paths: tuple[str, ...]


def _git(repo_root, args, *, binary=False):
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=not binary,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionError("git %s failed: %s" % (" ".join(args), exc)) from exc
    return completed.stdout


def _resolve_commit(repo_root, ref):
    sha = _git(repo_root, ["rev-parse", "%s^{commit}" % ref]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise PromotionError("cannot resolve commit %s" % ref)
    return sha


def _json_at(repo_root, ref, path):
    raw = _git(repo_root, ["show", "%s:%s" % (ref, path)], binary=True)
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError("invalid JSON at %s:%s: %s" % (ref, path, exc)) from exc


def _valid_date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _changed_files(repo_root, parent_sha, head_sha):
    output = _git(
        repo_root,
        ["diff-tree", "--no-commit-id", "--name-status", "-r", parent_sha, head_sha],
    )
    entries = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or fields[0] not in {"A", "M"}:
            raise PromotionError("unsupported changed-path record: %r" % line)
        entries.append((fields[0], fields[1]))
    if not entries:
        raise PromotionError("candidate commit has no changed files")
    return entries


def _allowed_publication_path(path):
    return bool(
        path in {INDEX_PATH, MANIFEST_PATH}
        or RANKING_PATH_RE.fullmatch(path)
        or MARKET_PATH_RE.fullmatch(path)
    )


def _manifest_dates(manifest, label):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise PromotionError("%s manifest schema_version must be 1" % label)
    dates = manifest.get("dates")
    if not isinstance(dates, list) or not dates or not all(_valid_date(x) for x in dates):
        raise PromotionError("%s manifest dates must be a non-empty YYYY-MM-DD list" % label)
    if len(dates) != len(set(dates)) or dates != sorted(dates, reverse=True):
        raise PromotionError("%s manifest dates must be unique and descending" % label)
    return dates


def verify_candidate(repo_root, branch, head, base="origin/main"):
    """Return a verified promotion candidate or raise :class:`PromotionError`.

    ``base`` is the freshly fetched remote ``main``.  A normal candidate must
    be exactly one commit ahead.  ``base == head`` is accepted as an
    idempotent replay when the direct-push path already won the race.
    """
    repo_root = Path(repo_root)
    if not isinstance(branch, str) or not CLAUDE_BRANCH_RE.fullmatch(branch):
        raise PromotionError("fallback promotion only accepts claude/* branches")

    head_sha = _resolve_commit(repo_root, head)
    base_sha = _resolve_commit(repo_root, base)
    parents = _git(repo_root, ["show", "-s", "--format=%P", head_sha]).strip().split()
    if len(parents) != 1:
        raise PromotionError("candidate must have exactly one parent")
    parent_sha = parents[0]
    status = "already-published" if head_sha == base_sha else "ready"
    if status == "ready" and parent_sha != base_sha:
        raise PromotionError(
            "candidate is not a direct child of current main (%s != %s)"
            % (parent_sha[:12], base_sha[:12])
        )

    entries = _changed_files(repo_root, parent_sha, head_sha)
    unexpected = [path for _status, path in entries if not _allowed_publication_path(path)]
    if unexpected:
        raise PromotionError("candidate changes non-publication paths: %s" % ", ".join(unexpected))
    changed = {path: change for change, path in entries}
    if changed.get(MANIFEST_PATH) != "M":
        raise PromotionError("candidate must modify docs/data/manifest.json")

    message = _git(repo_root, ["show", "-s", "--format=%s", head_sha]).strip()
    match = re.fullmatch(r"Update TSE day gainers (\d{4}-\d{2}-\d{2})", message)
    if not match or not _valid_date(match.group(1)):
        raise PromotionError("candidate commit message must name one valid session date")
    session = match.group(1)
    ranking_path = "docs/data/%s.json" % session
    if changed.get(ranking_path) not in {"A", "M"}:
        raise PromotionError("candidate must add or update %s" % ranking_path)
    market_path = "docs/data/%s_market.json" % session
    session_paths = {INDEX_PATH, MANIFEST_PATH, ranking_path, market_path}
    other_sessions = [path for path in changed if path not in session_paths]
    if other_sessions:
        raise PromotionError(
            "candidate changes files outside the named session: %s"
            % ", ".join(other_sessions)
        )

    manifest, _ = _json_at(repo_root, head_sha, MANIFEST_PATH)
    dates = _manifest_dates(manifest, "candidate")
    if dates[0] != session:
        raise PromotionError("candidate session must be the newest manifest date")

    parent_manifest, _ = _json_at(repo_root, parent_sha, MANIFEST_PATH)
    parent_dates = _manifest_dates(parent_manifest, "parent")
    if session in parent_dates or session <= parent_dates[0]:
        raise PromotionError("candidate session must be newer than the parent manifest")

    ranking, ranking_bytes = _json_at(repo_root, head_sha, ranking_path)
    try:
        prepared = publisher.prepare_ranking(ranking)
    except publisher.PublishError as exc:
        raise PromotionError("candidate ranking is not publishable: %s" % exc) from exc
    if prepared.get("session_date") != session:
        raise PromotionError("candidate ranking session_date does not match commit message")

    expected_digest = hashlib.sha256(ranking_bytes).hexdigest()
    try:
        manifest_ranking = manifest["artifacts"][session]["ranking"]
    except (KeyError, TypeError) as exc:
        raise PromotionError("candidate manifest lacks ranking metadata") from exc
    if manifest_ranking.get("path") != "%s.json" % session:
        raise PromotionError("candidate manifest ranking path is incorrect")
    if manifest_ranking.get("sha256") != expected_digest:
        raise PromotionError("candidate manifest digest does not match ranking bytes")

    if changed.get(market_path) in {"A", "M"}:
        market, _ = _json_at(repo_root, head_sha, market_path)
        if not isinstance(market, dict) or market.get("schema_version") != 1:
            raise PromotionError("candidate market sidecar schema_version must be 1")
        if market.get("session_date") != session:
            raise PromotionError("candidate market sidecar session_date is incorrect")

    return PromotionCandidate(
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        parent_sha=parent_sha,
        session=session,
        status=status,
        changed_paths=tuple(path for _change, path in entries),
    )
