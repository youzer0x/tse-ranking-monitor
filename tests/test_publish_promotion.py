"""Fail-closed tests for Claude-branch publication promotion."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tse_ranking_monitor.publishing import promotion


ROOT = Path(__file__).resolve().parents[1]


def _run(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _ranking(session):
    return {
        "session_date": session,
        "session_window": "%s 09:00–15:30 JST" % session,
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
            "factor": "材料を確認",
            "factor_kind": "報道",
        }],
    }


def _manifest(data_dir, dates):
    artifacts = {}
    for session in dates:
        path = data_dir / (session + ".json")
        artifacts[session] = {"ranking": {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }}
    return {"schema_version": 1, "dates": dates, "artifacts": artifacts}


def _repo_with_candidate(tmp_path, history=("2026-07-16",), market_history=()):
    """Build a repo whose claude/* branch holds one publication commit.

    ``history`` seeds the parent manifest newest-first so retention-pruning
    cases have an older tail to drop; ``market_history`` additionally seeds
    market sidecars for those dates.
    """
    history = list(history)
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.name", "Test")
    _run(repo, "config", "user.email", "test@example.com")
    data_dir = repo / "docs" / "data"
    for session in history:
        _write_json(data_dir / (session + ".json"), _ranking(session))
    for session in market_history:
        _write_json(data_dir / (session + "_market.json"), {
            "schema_version": 1,
            "session_date": session,
        })
    _write_json(data_dir / "manifest.json", _manifest(data_dir, history))
    (repo / "docs" / "index.html").write_text("<html>base</html>\n", encoding="utf-8")
    _run(repo, "add", "docs")
    _run(repo, "commit", "-m", "base")
    base = _run(repo, "rev-parse", "HEAD")

    _run(repo, "switch", "-c", "claude/test-session")
    (repo / "docs" / "index.html").write_text("<html>2026-07-17</html>\n", encoding="utf-8")
    _write_json(data_dir / "2026-07-17.json", _ranking("2026-07-17"))
    _write_json(data_dir / "2026-07-17_market.json", {
        "schema_version": 1,
        "session_date": "2026-07-17",
    })
    _write_json(
        data_dir / "manifest.json",
        _manifest(data_dir, ["2026-07-17"] + history),
    )
    _run(repo, "add", "docs")
    _run(repo, "commit", "-m", "Update TSE day gainers 2026-07-17")
    head = _run(repo, "rev-parse", "HEAD")
    return repo, base, head


def _prune(repo, sessions, remaining):
    """Delete the given sessions' artifacts and re-commit the amended candidate.

    Mirrors ``publisher.cleanup_old`` + ``update_manifest``: the files go, then
    the manifest is rebuilt from what is left on disk.
    """
    data_dir = repo / "docs" / "data"
    for session in sessions:
        (data_dir / (session + ".json")).unlink()
        sidecar = data_dir / (session + "_market.json")
        if sidecar.exists():
            sidecar.unlink()
    _write_json(data_dir / "manifest.json", _manifest(data_dir, remaining))
    _run(repo, "add", "-A", "docs")
    _run(repo, "commit", "--amend", "--no-edit")
    return _run(repo, "rev-parse", "HEAD")


def test_accepts_direct_child_docs_only_publication(tmp_path):
    repo, base, head = _repo_with_candidate(tmp_path)

    candidate = promotion.verify_candidate(repo, "claude/test-session", head, base)

    assert candidate.status == "ready"
    assert candidate.session == "2026-07-17"
    assert candidate.base_sha == base
    assert candidate.head_sha == head


def test_accepts_idempotent_replay_after_direct_push_won(tmp_path):
    repo, _base, head = _repo_with_candidate(tmp_path)

    candidate = promotion.verify_candidate(repo, "claude/test-session", head, head)

    assert candidate.status == "already-published"


def test_rejects_non_claude_branch(tmp_path):
    repo, base, head = _repo_with_candidate(tmp_path)

    with pytest.raises(promotion.PromotionError, match=r"claude/\*"):
        promotion.verify_candidate(repo, "feature/test", head, base)


def test_rejects_non_publication_path(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path)
    (repo / "README.md").write_text("unexpected\n", encoding="utf-8")
    _run(repo, "add", "README.md")
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="non-publication"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_candidate_not_based_on_current_main(tmp_path):
    repo, _base, head = _repo_with_candidate(tmp_path)
    _run(repo, "switch", "main")
    (repo / "README.md").write_text("main moved\n", encoding="utf-8")
    _run(repo, "add", "README.md")
    _run(repo, "commit", "-m", "main moved")
    moved_main = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="direct child"):
        promotion.verify_candidate(repo, "claude/test-session", head, moved_main)


def test_rejects_manifest_digest_mismatch(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path)
    manifest_path = repo / "docs" / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["2026-07-17"]["ranking"]["sha256"] = "0" * 64
    _write_json(manifest_path, manifest)
    _run(repo, "add", str(manifest_path.relative_to(repo)))
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="digest"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_rewriting_a_historical_publication(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path)
    old_path = repo / "docs" / "data" / "2026-07-16.json"
    old_ranking = json.loads(old_path.read_text(encoding="utf-8"))
    old_ranking["rows"][0]["factor"] = "過去日の改変"
    _write_json(old_path, old_ranking)
    _run(repo, "add", str(old_path.relative_to(repo)))
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="outside the named session"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


# --- retention pruning (publisher.cleanup_old emits `D` records) ---------------

HISTORY = ["2026-07-16", "2026-07-15", "2026-07-14", "2026-07-13"]


def test_accepts_retention_pruning_of_the_oldest_sessions(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path, history=HISTORY)
    head = _prune(repo, ["2026-07-13"], ["2026-07-17", "2026-07-16", "2026-07-15", "2026-07-14"])

    candidate = promotion.verify_candidate(repo, "claude/test-session", head, base)

    assert candidate.status == "ready"
    assert candidate.session == "2026-07-17"
    assert candidate.deleted_paths == ("docs/data/2026-07-13.json",)


def test_accepts_pruning_ranking_and_market_sidecar_together(tmp_path):
    repo, base, _head = _repo_with_candidate(
        tmp_path, history=HISTORY, market_history=["2026-07-13"]
    )
    head = _prune(repo, ["2026-07-13"], ["2026-07-17", "2026-07-16", "2026-07-15", "2026-07-14"])

    candidate = promotion.verify_candidate(repo, "claude/test-session", head, base)

    assert candidate.deleted_paths == (
        "docs/data/2026-07-13.json",
        "docs/data/2026-07-13_market.json",
    )


def test_rejects_deleting_the_index_page(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path, history=HISTORY)
    (repo / "docs" / "index.html").unlink()
    _run(repo, "add", "-A", "docs")
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="non-artifact path"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_pruning_a_session_still_inside_the_window(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path, history=HISTORY)
    head = _prune(repo, ["2026-07-16"], ["2026-07-17", "2026-07-15", "2026-07-14", "2026-07-13"])

    with pytest.raises(promotion.PromotionError, match="only the oldest sessions"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_pruning_that_punches_a_hole_in_the_window(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path, history=HISTORY)
    head = _prune(repo, ["2026-07-15"], ["2026-07-17", "2026-07-16", "2026-07-14", "2026-07-13"])

    with pytest.raises(promotion.PromotionError, match="only the oldest sessions"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_deleted_file_that_the_manifest_still_lists(tmp_path):
    repo, base, _head = _repo_with_candidate(tmp_path, history=HISTORY)
    data_dir = repo / "docs" / "data"
    stale = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    (data_dir / "2026-07-13.json").unlink()
    _write_json(data_dir / "manifest.json", stale)  # manifest keeps the pruned date
    _run(repo, "add", "-A", "docs")
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="still lists pruned sessions"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_deleting_a_market_sidecar_whose_ranking_survives(tmp_path):
    repo, base, _head = _repo_with_candidate(
        tmp_path, history=HISTORY, market_history=["2026-07-13"]
    )
    (repo / "docs" / "data" / "2026-07-13_market.json").unlink()
    _run(repo, "add", "-A", "docs")
    _run(repo, "commit", "--amend", "--no-edit")
    head = _run(repo, "rev-parse", "HEAD")

    with pytest.raises(promotion.PromotionError, match="ranking survives"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_rejects_pruning_more_sessions_than_the_limit(tmp_path):
    history = ["2026-07-16"] + [
        "2026-06-%02d" % day for day in range(30, 30 - promotion.MAX_PRUNED_SESSIONS - 1, -1)
    ]
    repo, base, _head = _repo_with_candidate(tmp_path, history=history)
    head = _prune(repo, history[1:], ["2026-07-17", "2026-07-16"])

    with pytest.raises(promotion.PromotionError, match="above the .* limit"):
        promotion.verify_candidate(repo, "claude/test-session", head, base)


def test_workflow_keeps_promotion_and_pages_build_fail_closed():
    validation = (
        ROOT / ".github" / "workflows" / "validate-routine-publication.yml"
    ).read_text(encoding="utf-8")
    promotion_workflow = (
        ROOT / ".github" / "workflows" / "promote-routine-publication.yml"
    ).read_text(encoding="utf-8")

    assert 'branches:\n      - "claude/**"' in validation
    assert 'paths:\n      - "docs/**"' in validation
    assert "contents: read" in validation
    assert "contents: write" not in validation
    assert "verify_publish_candidate.py" in validation

    assert 'workflow_run:' in promotion_workflow
    assert '"Validate routine publication"' in promotion_workflow
    assert "conclusion == 'success'" in promotion_workflow
    assert "contents: write" in promotion_workflow
    assert "pages: write" in promotion_workflow
    assert "verify_publish_candidate.py" in promotion_workflow
    assert "github.event.workflow_run.head_sha" in promotion_workflow
    assert ':refs/heads/main"' in promotion_workflow
    assert '"repos/$GITHUB_REPOSITORY/pages/builds"' in promotion_workflow
