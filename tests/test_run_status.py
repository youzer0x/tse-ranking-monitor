"""Durable run status: real git plumbing against a bare remote, no network."""
import json
import subprocess

import pytest

from tse_ranking_monitor.runtime import status as run_status
from tse_ranking_monitor.runtime import telemetry


def _run(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo_with_remote(tmp_path):
    """A work repo whose origin is a bare repo, mirroring the routine's setup."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run(remote, "init", "--bare", "-b", "main")

    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.name", "Test")
    _run(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _run(repo, "add", "tracked.txt")
    _run(repo, "commit", "-m", "base")
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "origin", "main")
    return repo, remote


def _remote_file(remote, branch, path):
    return _run(remote, "show", "%s:%s" % (branch, path))


def test_publish_status_lands_on_the_branch_without_disturbing_the_checkout(tmp_path):
    repo, remote = _repo_with_remote(tmp_path)
    # The routine holds uncommitted publication artifacts when status is written.
    (repo / "docs").mkdir()
    (repo / "docs" / "pending.json").write_text("{}\n", encoding="utf-8")
    _run(repo, "add", "docs/pending.json")
    head_before = _run(repo, "rev-parse", "HEAD")
    status_before = _run(repo, "status", "--porcelain")

    status = run_status.build_status("2026-07-27", last_stage="research")
    commit = run_status.publish_status(repo, status)

    assert len(commit) == 40
    assert _run(repo, "rev-parse", "HEAD") == head_before
    assert _run(repo, "status", "--porcelain") == status_before
    assert _run(repo, "branch", "--show-current") == "main"

    published = json.loads(
        _remote_file(remote, run_status.STATUS_BRANCH, "status/2026-07-27.json"))
    assert published["session"] == "2026-07-27"
    assert published["last_stage"] == "research"
    assert published["delivered"] is False
    latest = json.loads(_remote_file(remote, run_status.STATUS_BRANCH, "status/latest.json"))
    assert latest == published


def test_second_publish_extends_the_existing_branch(tmp_path):
    repo, remote = _repo_with_remote(tmp_path)

    first = run_status.publish_status(repo, run_status.build_status("2026-07-27"))
    second = run_status.publish_status(
        repo, run_status.build_status("2026-07-27", delivered=True))

    assert second != first
    assert _run(repo, "rev-parse", "%s^" % second) == first
    published = json.loads(
        _remote_file(remote, run_status.STATUS_BRANCH, "status/2026-07-27.json"))
    assert published["delivered"] is True


def test_publish_keeps_earlier_sessions_on_the_branch(tmp_path):
    repo, remote = _repo_with_remote(tmp_path)

    run_status.publish_status(repo, run_status.build_status("2026-07-24", delivered=True))
    run_status.publish_status(repo, run_status.build_status("2026-07-27"))

    older = json.loads(
        _remote_file(remote, run_status.STATUS_BRANCH, "status/2026-07-24.json"))
    assert older["delivered"] is True


def test_publish_status_quietly_swallows_an_unreachable_remote(tmp_path, capsys):
    """A dead remote (or a push restriction) must cost detail, never the delivery."""
    repo, _remote = _repo_with_remote(tmp_path)
    _run(repo, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))

    result = run_status.publish_status_quietly(repo, run_status.build_status("2026-07-27"))

    assert result is None
    err = capsys.readouterr().err
    assert "ステータスの公開に失敗" in err
    assert "ls-remote" in err  # the failure is diagnosed before any commit is minted


def _single_branch_clone(tmp_path, remote, name):
    """A fresh clone of ``main`` only -- what the cloud routine starts from."""
    clone = tmp_path / name
    # --no-local: a path clone would otherwise copy the whole object store and
    # hide the very gap (status objects absent) this fixture exists to model.
    _run(tmp_path, "clone", "--quiet", "--no-local", "--single-branch", "--branch", "main",
         str(remote), str(clone))
    _run(clone, "config", "user.name", "Test")
    _run(clone, "config", "user.email", "test@example.com")
    return clone


def test_publish_from_a_fresh_clone_extends_the_remote_branch(tmp_path):
    """The clone has none of the status branch's objects until they are fetched."""
    repo, remote = _repo_with_remote(tmp_path)
    first = run_status.publish_status(repo, run_status.build_status("2026-07-27"))
    clone = _single_branch_clone(tmp_path, remote, "clone")
    # The tip object is absent locally: exactly the state that broke read-tree.
    assert subprocess.run(
        ["git", "cat-file", "-e", first], cwd=clone, capture_output=True
    ).returncode != 0

    second = run_status.publish_status(
        clone, run_status.build_status("2026-07-28", last_stage="stage2"))

    assert _run(clone, "rev-parse", "%s^" % second) == first
    assert _run(remote, "rev-parse", run_status.STATUS_BRANCH) == second
    latest = json.loads(_remote_file(remote, run_status.STATUS_BRANCH, "status/latest.json"))
    assert latest["session"] == "2026-07-28"
    older = json.loads(_remote_file(remote, run_status.STATUS_BRANCH, "status/2026-07-27.json"))
    assert older["session"] == "2026-07-27"


def test_fresh_clone_without_a_remote_status_branch_starts_the_branch(tmp_path):
    _repo, remote = _repo_with_remote(tmp_path)
    clone = _single_branch_clone(tmp_path, remote, "clone")

    commit = run_status.publish_status(clone, run_status.build_status("2026-07-27"))

    assert _run(clone, "rev-list", "--count", commit) == "1"
    assert _run(remote, "rev-parse", run_status.STATUS_BRANCH) == commit


def test_fetch_failure_with_an_existing_branch_is_an_error_not_an_orphan(tmp_path, monkeypatch):
    """If the tip cannot be fetched the run must not mint a parentless commit."""
    repo, remote = _repo_with_remote(tmp_path)
    first = run_status.publish_status(repo, run_status.build_status("2026-07-27"))
    real = run_status._git_result

    def failing_fetch(repo_root, args, **kwargs):
        if args and args[0] == "fetch":
            return subprocess.CompletedProcess(
                ["git", *args], 128, stdout="", stderr="fatal: simulated fetch failure")
        return real(repo_root, args, **kwargs)

    monkeypatch.setattr(run_status, "_git_result", failing_fetch)

    with pytest.raises(run_status.StatusError, match="simulated fetch failure"):
        run_status.publish_status(repo, run_status.build_status("2026-07-27", delivered=True))

    assert _run(remote, "rev-parse", run_status.STATUS_BRANCH) == first


def test_publish_status_rejects_a_document_without_a_session(tmp_path):
    repo, _remote = _repo_with_remote(tmp_path)

    with pytest.raises(run_status.StatusError, match="no session"):
        run_status.publish_status(repo, {"schema_version": 1})


def test_collect_status_reports_the_stage_the_session_was_inside(tmp_path):
    writer = telemetry.TelemetryWriter(tmp_path)
    telemetry.write_session_pointer(tmp_path, "2026-07-27", run_id="run-7")
    writer.record_stage("2026-07-27", "stage1", "start")
    writer.record_stage("2026-07-27", "stage1", "end")
    writer.record_stage("2026-07-27", "research", "start")

    status = run_status.collect_status(tmp_path, "2026-07-27", died_at="research")

    assert status["last_stage"] == "research"
    assert status["died_at"] == "research"
    assert status["stages"] == ["research"]
    assert status["run_id"] == "run-7"
    assert status["started_at"]
    assert status["delivered"] is False


def test_collect_status_of_a_completed_run_names_no_stage(tmp_path):
    writer = telemetry.TelemetryWriter(tmp_path)
    writer.record_stage("2026-07-27", "publish", "start")
    writer.record_stage("2026-07-27", "publish", "end")

    status = run_status.collect_status(tmp_path, "2026-07-27", delivered=True)

    assert status["delivered"] is True
    assert "last_stage" not in status
    assert "died_at" not in status
