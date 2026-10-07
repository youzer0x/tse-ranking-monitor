import json
import subprocess
from pathlib import Path

import pytest
from tse_ranking_monitor import pipeline
from tse_ranking_monitor.research.plan import _atomic_write_json
from test_publish import _ranking
from publication_fixtures import research_bundle


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "verify_contract_lock", lambda root: [])
    monkeypatch.setattr(pipeline, "fingerprint", lambda root: "version1")
    monkeypatch.setattr(pipeline, "archive_session", lambda *args, **kwargs: None)
    return pipeline.Pipeline(tmp_path, "2026-07-15")


def test_stage_order_and_tampered_checkpoint_fail_closed(run):
    with pytest.raises(ValueError, match="complete research"):
        run.execute("publish", "./")
    stage1 = run.work / "stage1.json"
    _atomic_write_json(stage1, _ranking())
    run.save("start", [stage1])
    stage1.write_text("{}")
    with pytest.raises(ValueError, match="checkpoint changed"):
        run.execute("research", "./")


def test_skip_and_timeout_do_not_generate(run, monkeypatch):
    calls = []
    def skip(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, "SKIP\n", "")
    monkeypatch.setattr(run, "script", skip)
    run.execute("start", "./")
    assert len(calls) == 1
    assert not run.state["completed"]
    assert not (run.root / "docs").exists()
    monkeypatch.setattr(run, "script", lambda *a, **k: subprocess.CompletedProcess([], 2, "TIMEOUT\n", ""))
    with pytest.raises(ValueError, match="gate"):
        run.execute("start", "./")
    assert not (run.work / "stage1.json").exists()


def test_resume_reuses_completed_stage_without_fetch(run, monkeypatch):
    source = run.work / "stage1.json"
    _atomic_write_json(source, _ranking())
    run.save("start", [source])
    monkeypatch.setattr(run, "script", lambda *a, **k: pytest.fail("should not refetch"))
    run.execute("start", "./")
    monkeypatch.setattr(pipeline, "fingerprint", lambda root: "version2")
    with pytest.raises(ValueError, match="code/config changed"):
        pipeline.Pipeline(run.root, run.session)


def test_missing_market_does_not_prevent_publication(run):
    ranking = _ranking()
    path = run.work / "ranking.json"
    _atomic_write_json(path, ranking)
    research_bundle(ranking, run.research_dir)
    run.save("research", [path], market_failure=None)
    run.execute("publish", "./")
    assert run.completed("publish")
    assert run.state["completed"]["publish"]["market_failure"]
    assert (run.root / "docs/data/2026-07-15.json").exists()
    assert not (run.root / "docs/data/2026-07-15_market.json").exists()


@pytest.mark.parametrize("phase", pipeline.PHASES)
def test_every_phase_name_resolves_to_a_bound_method(run, phase):
    """2026-10-06/07: ``self.research`` (a Path) shadowed ``research()`` and
    ``execute("research")`` raised TypeError on two consecutive routine runs.
    Any future attribute named after a phase must fail here, not in production."""
    attribute = getattr(run, phase)
    assert callable(attribute), f"{phase} is shadowed by a {type(attribute).__name__}"


def test_research_phase_dispatches_to_method_not_directory_attribute(run, monkeypatch):
    stage1 = run.work / "stage1.json"
    _atomic_write_json(stage1, _ranking())
    run.save("start", [stage1])
    monkeypatch.setattr(run, "script", lambda *a, **k: subprocess.CompletedProcess([], 0, "{}", ""))
    monkeypatch.setattr(pipeline, "require_compiled_evidence", lambda *a, **k: None)
    run.execute("research", "./")
    assert run.completed("research")
    assert run.state["completed"]["research"]["market_failure"] is None


def test_notify_stage_records_delivery_in_the_durable_status(run, monkeypatch):
    """The notify stage's end-of-stage status used to overwrite the
    ``delivered: true`` that ``mark_delivered`` had just pushed (2026-10-06)."""
    stage1 = run.work / "stage1.json"
    _atomic_write_json(stage1, _ranking())
    ranking = run.work / "ranking.json"
    _atomic_write_json(ranking, _ranking())
    run.save("start", [stage1])
    run.save("research", [ranking], market_failure=None)
    run.save("publish", [])
    run.save("deploy", revision="abc")
    monkeypatch.setattr(run, "run", lambda args, **k: subprocess.CompletedProcess(args, 0, "abc\n", ""))
    monkeypatch.setattr(pipeline.publisher, "notify",
                        lambda input_path, docs_dir, pages_url: pipeline.publisher.mark_delivered(run.session, run.root))
    statuses = []
    monkeypatch.setattr(pipeline.run_status, "publish_status_quietly",
                        lambda root, status, **k: statuses.append(status))

    run.execute("notify", "./")

    assert run.completed("notify")
    assert [status["delivered"] for status in statuses] == [False, True, True]


def test_runtime_contract_failure_blocks_all_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "verify_contract_lock", lambda root: ["hash mismatch"])
    with pytest.raises(ValueError, match="runtime contract"):
        pipeline.Pipeline(tmp_path, "2026-07-15")


@pytest.mark.parametrize("main_rejected", [False, True])
def test_deploy_resumes_after_commit_and_preserves_exact_candidate(tmp_path, monkeypatch, main_rejected):
    from test_publish_promotion import _repo_with_candidate, _run
    repo, base, head = _repo_with_candidate(tmp_path)
    remote = tmp_path / "remote.git"
    _run(repo, "clone", "--bare", str(repo), str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(pipeline, "verify_contract_lock", lambda root: [])
    monkeypatch.setattr(pipeline, "fingerprint", lambda root: "version1")
    monkeypatch.setattr(pipeline.Pipeline, "mark_stage", lambda *args: None)
    flow = pipeline.Pipeline(repo, "2026-07-17")
    ranking = json.loads((repo / "docs/data/2026-07-17.json").read_text(encoding="utf-8"))
    _atomic_write_json(flow.work / "ranking.json", ranking)
    research_bundle(ranking, flow.research_dir)
    flow.save("publish", list((repo / "docs/data").glob("*.json")) + [repo / "docs/index.html"])
    real_run = flow.run
    pushes = []
    def run_cmd(arguments, **kwargs):
        if arguments[:2] == ["git", "push"]:
            pushes.append(arguments)
            if main_rejected and arguments[-1] == "HEAD:main":
                return subprocess.CompletedProcess(arguments, 1, "", "main denied")
        return real_run(arguments, **kwargs)
    monkeypatch.setattr(flow, "run", run_cmd)
    flow.execute("deploy", "./")
    assert flow.state["completed"]["deploy"]["revision"] == head
    assert _run(repo, "rev-parse", "HEAD") == head
    assert len(pushes) == (2 if main_rejected else 1)
    assert _run(remote, "rev-parse", "main") == (base if main_rejected else head)
    flow.execute("deploy", "./")
    assert len(pushes) == (2 if main_rejected else 1)
