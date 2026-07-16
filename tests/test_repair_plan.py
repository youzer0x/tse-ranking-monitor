"""Repair re-planning tests: repair_context injection, state machine, strict gates."""

import json

import pytest

from tse_ranking_monitor.research.evidence import (
    ResearchValidationError,
    compile_research_results,
)
from tse_ranking_monitor.research.plan import (
    _canonical_digest,
    main as plan_main,
    write_research_plan,
)
from tse_ranking_monitor.research.repair import (
    REPAIR_CARRY_FIELDS,
    apply_repair_targets,
    main as repair_main,
)


def _row(code, rank, **overrides):
    row = {
        "code": code,
        "name": f"Company {code}",
        "rank": rank,
        "pct": 8.0,
        "pct5": 3.0,
        "turnover_m": 500.0,
        "disclosures": [],
        "kabutan_news": [],
        "factor": "",
        "factor_kind": "",
    }
    row.update(overrides)
    return row


def _tob_news(code):
    """In-window TOB headline -> m_and_a risk reason, requires_edinet item."""
    return [{
        "datetime": "2026-07-15T10:00:00+09:00",
        "category": "材料",
        "title": "TOB観測で急伸",
        "url": f"https://kabutan.jp/news/tob-{code}",
    }]


def _high_row(code, rank):
    """large_move (pct>=15) -> high risk, route deep."""
    return _row(code, rank, pct=20.0)


def _ranking(rows):
    return {
        "session_date": "2026-07-15",
        "prev_date": "2026-07-14",
        "rows": rows,
        "theme_clusters": [],
    }


def _valid_result(entry):
    return {
        "schema_version": "research_batch_result.v1",
        "batch_id": entry["batch_id"],
        "input_digest": entry["input_digest"],
        "items": [{
            "code": code,
            "status": "complete",
            "confidence": "medium",
            "factor": "テーマ物色と並走したとみられる。",
            "factor_kind": "テーマ",
            "claims": [{"text": "テーマ物色", "source_ids": []}],
            "sources": [],
            "checks": {
                "disclosures": "done",
                "kabutan_news": "done",
                "web_search": "done",
                "sector_cluster": "na",
                "edinet": "na",
            },
            "market_note": "テーマ物色と並走。",
        } for code in entry["codes"]],
    }


def _write_result(research_dir, entry, result=None):
    path = research_dir / entry["result_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result if result is not None else _valid_result(entry), ensure_ascii=False),
        encoding="utf-8",
    )


def _target(code, rule_ids=("RANK_UNSOURCED_CAUSAL",), messages=("因果表現に出典が無い",)):
    return {
        "code": code,
        "path": f"rows[0]({code}/Company {code})",
        "rule_ids": list(rule_ids),
        "severities": ["WARN"],
        "messages": list(messages),
    }


def _payload(*targets, validator="ranking"):
    return {
        "schema_version": "quality_findings.v1",
        "validator": validator,
        "files": [{"file": "docs/data/2026-07-15.json", "targets": list(targets)}],
    }


def _manifest(research_dir):
    return json.loads((research_dir / "manifest.json").read_text(encoding="utf-8"))


def _batch(research_dir, entry):
    return json.loads((research_dir / entry["path"]).read_text(encoding="utf-8"))


def _setup(tmp_path):
    """Two-batch plan (normal deep rows, 3+1) with valid completed checkpoints."""
    research_dir = tmp_path / "research"
    ranking = _ranking([_row(f"710{index}", index) for index in range(1, 5)])
    manifest = write_research_plan(ranking, research_dir)
    for entry in manifest["batches"]:
        _write_result(research_dir, entry)
    manifest = write_research_plan(ranking, research_dir)  # statuses -> complete
    assert [entry["status"] for entry in manifest["batches"]] == ["complete", "complete"]
    return research_dir, ranking, manifest


def _apply_cli(research_dir, payload, tmp_path, max_attempts=None):
    path = tmp_path / "repair-targets.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    argv = ["--research-dir", str(research_dir), "--repair-targets", str(path)]
    if max_attempts is not None:
        argv += ["--max-attempts", str(max_attempts)]
    return repair_main(argv)


def test_injection_carries_findings_and_previous_output(tmp_path):
    research_dir, _, manifest = _setup(tmp_path)
    affected, other = manifest["batches"][0], manifest["batches"][1]
    other_bytes = (research_dir / other["path"]).read_bytes()

    summary = apply_repair_targets(research_dir, _payload(_target("7102")))

    assert summary == {
        "injected": [affected["batch_id"]],
        "merged": [],
        "noop": [],
        "attempts": {affected["batch_id"]: 1},
    }
    batch = _batch(research_dir, affected)
    context = batch["repair_context"]
    assert context["attempt"] == 1
    assert [target["code"] for target in context["targets"]] == ["7102"]
    target = context["targets"][0]
    assert target["rule_ids"] == ["RANK_UNSOURCED_CAUSAL"]
    assert target["severities"] == ["WARN"]
    assert target["messages"] == ["因果表現に出典が無い"]
    assert set(target["previous"]) == set(REPAIR_CARRY_FIELDS)
    assert target["previous"]["code"] == "7102"
    assert target["previous"]["factor"] == "テーマ物色と並走したとみられる。"
    assert [item["code"] for item in context["carry_forward"]] == ["7101", "7103"]
    assert all(set(item) == set(REPAIR_CARRY_FIELDS) for item in context["carry_forward"])

    digest = batch.pop("input_digest")
    assert digest != affected["input_digest"]
    assert digest == _canonical_digest(batch)  # plan.py digest recipe, by hand

    updated = _manifest(research_dir)
    entry = updated["batches"][0]
    assert entry["status"] == "pending"
    assert entry["repair_attempts"] == 1
    assert entry["input_digest"] == digest
    assert entry["input_bytes"] == (research_dir / affected["path"]).stat().st_size
    assert updated["batches"][1] == other  # untouched entry, still complete
    assert (research_dir / other["path"]).read_bytes() == other_bytes
    assert updated["input_digest"] != manifest["input_digest"]
    assert updated["input_digest"] == _canonical_digest({
        "session_date": updated["session_date"],
        "codes": updated["ranking_codes"],
        "batches": [item["input_digest"] for item in updated["batches"]],
    })
    # The old checkpoint stays on disk; the digest change alone invalidates it.
    assert (research_dir / affected["result_path"]).exists()


def test_injected_batch_stale_result_is_rejected_end_to_end(tmp_path):
    research_dir, _, _ = _setup(tmp_path)

    apply_repair_targets(research_dir, _payload(_target("7102")))

    with pytest.raises(ResearchValidationError, match="stale"):
        compile_research_results(research_dir, strict=True)


def test_identical_targets_on_pending_batch_are_noop(tmp_path):
    research_dir, _, manifest = _setup(tmp_path)
    batch_id = manifest["batches"][0]["batch_id"]
    payload = _payload(_target("7102"))
    apply_repair_targets(research_dir, payload)
    manifest_bytes = (research_dir / "manifest.json").read_bytes()
    entry = _manifest(research_dir)["batches"][0]
    batch_bytes = (research_dir / entry["path"]).read_bytes()

    summary = apply_repair_targets(research_dir, payload)

    assert summary == {
        "injected": [],
        "merged": [],
        "noop": [batch_id],
        "attempts": {batch_id: 1},
    }
    assert (research_dir / "manifest.json").read_bytes() == manifest_bytes
    assert (research_dir / entry["path"]).read_bytes() == batch_bytes


def test_new_findings_merge_into_pending_context_without_increment(tmp_path):
    research_dir, _, manifest = _setup(tmp_path)
    batch_id = manifest["batches"][0]["batch_id"]
    apply_repair_targets(research_dir, _payload(_target("7102")))

    summary = apply_repair_targets(research_dir, _payload(
        _target("7102", rule_ids=("RANK_UNSOURCED_REPORT",),
                messages=("報道帰属に出典が無い",)),
        _target("7101"),
    ))

    assert summary["merged"] == [batch_id]
    assert summary["attempts"] == {batch_id: 1}
    entry = _manifest(research_dir)["batches"][0]
    assert entry["repair_attempts"] == 1  # merge never consumes an attempt
    context = _batch(research_dir, entry)["repair_context"]
    assert context["attempt"] == 1
    assert [target["code"] for target in context["targets"]] == ["7101", "7102"]
    merged_target = context["targets"][1]
    assert merged_target["rule_ids"] == ["RANK_UNSOURCED_CAUSAL", "RANK_UNSOURCED_REPORT"]
    assert merged_target["messages"] == ["因果表現に出典が無い", "報道帰属に出典が無い"]
    # 7101 moved from carry_forward into targets, keeping its previous output.
    assert context["targets"][0]["previous"]["code"] == "7101"
    assert [item["code"] for item in context["carry_forward"]] == ["7103"]


def test_recompleted_batch_reinjects_until_budget_then_exit_3(tmp_path):
    research_dir, _, manifest = _setup(tmp_path)
    batch_id = manifest["batches"][0]["batch_id"]
    payload = _payload(_target("7102"))
    apply_repair_targets(research_dir, payload)

    # A fresh valid result for the repaired digest completes the batch again;
    # re-applying the finding invalidates that completed work -> attempt 2.
    entry = _manifest(research_dir)["batches"][0]
    _write_result(research_dir, entry)
    summary = apply_repair_targets(research_dir, payload)
    assert summary["injected"] == [batch_id]
    assert summary["attempts"] == {batch_id: 2}
    assert _manifest(research_dir)["batches"][0]["repair_attempts"] == 2

    # Third completed application exceeds max_attempts=2: exit 3, no writes.
    entry = _manifest(research_dir)["batches"][0]
    _write_result(research_dir, entry)
    watched = [research_dir / "manifest.json"] + [
        research_dir / item["path"] for item in _manifest(research_dir)["batches"]
    ]
    before = {path: path.read_bytes() for path in watched}

    assert _apply_cli(research_dir, payload, tmp_path) == 3

    for path, content in before.items():
        assert path.read_bytes() == content


def test_repair_never_touches_dispatch_ledger_or_budget(tmp_path):
    research_dir, _, _ = _setup(tmp_path)
    manifest_path = research_dir / "manifest.json"
    manifest = _manifest(research_dir)
    spent = {"reservations": {"batch-001": 2}, "total_reserved": 2}
    manifest["ledger"] = spent
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    apply_repair_targets(research_dir, _payload(_target("7102")))

    updated = _manifest(research_dir)
    assert updated["ledger"] == spent
    assert updated["dispatch_budget"] == manifest["dispatch_budget"]


def test_cli_fails_closed_on_unknown_code_wrong_validator_and_empty_targets(tmp_path):
    research_dir, _, _ = _setup(tmp_path)
    manifest_bytes = (research_dir / "manifest.json").read_bytes()

    assert _apply_cli(research_dir, _payload(_target("9999")), tmp_path) == 1
    assert _apply_cli(
        research_dir, _payload(_target("7102"), validator="market"), tmp_path
    ) == 1
    path_only = {"code": None, "path": "overview.points[0]",
                 "rule_ids": ["MKT_UNSOURCED_CAUSAL"], "severities": ["WARN"],
                 "messages": ["因果表現"]}
    assert _apply_cli(research_dir, _payload(path_only), tmp_path) == 4
    assert _apply_cli(research_dir, _payload(), tmp_path) == 4

    assert (research_dir / "manifest.json").read_bytes() == manifest_bytes


def test_injected_batch_input_bytes_stay_bounded(tmp_path):
    research_dir, _, _ = _setup(tmp_path)

    apply_repair_targets(research_dir, _payload(_target("7102")))

    entry = _manifest(research_dir)["batches"][0]
    assert entry["input_bytes"] == (research_dir / entry["path"]).stat().st_size
    assert entry["input_bytes"] < 20000  # guards unbounded repair context


def _edinet_plan(tmp_path):
    """One pooled high-risk batch: an m_and_a row plus two large-move rows."""
    research_dir = tmp_path / "research"
    rows = [
        _row("3001", 1, kabutan_news=_tob_news("3001")),
        _high_row("3002", 2),
        _high_row("3003", 3),
    ]
    manifest = write_research_plan(_ranking(rows), research_dir)
    assert len(manifest["batches"]) == 1
    return research_dir, manifest["batches"][0]


def test_requires_edinet_item_cannot_report_na(tmp_path):
    research_dir, entry = _edinet_plan(tmp_path)
    result = _valid_result(entry)
    for item in result["items"]:  # only the m_and_a item keeps edinet=na
        if item["code"] != "3001":
            item["checks"]["edinet"] = "done"
    _write_result(research_dir, entry, result)

    with pytest.raises(ResearchValidationError) as exc_info:
        compile_research_results(research_dir, strict=True)

    assert any("requires_edinet" in error and "3001" in error
               for error in exc_info.value.errors)
    assert not any("requires_edinet" in error and ("3002" in error or "3003" in error)
                   for error in exc_info.value.errors)


@pytest.mark.parametrize("state", ["done", "unavailable"])
def test_requires_edinet_accepts_done_and_unavailable(tmp_path, state):
    research_dir, entry = _edinet_plan(tmp_path)
    result = _valid_result(entry)
    for item in result["items"]:
        item["checks"]["edinet"] = state if item["code"] == "3001" else "na"
    _write_result(research_dir, entry, result)

    evidence, _ = compile_research_results(research_dir, strict=True)

    assert evidence["complete"] is True


def test_carry_forward_drift_blocks_strict_compile(tmp_path):
    research_dir, _, _ = _setup(tmp_path)
    apply_repair_targets(research_dir, _payload(_target("7102")))
    entry = _manifest(research_dir)["batches"][0]

    drifted = _valid_result(entry)
    for item in drifted["items"]:
        if item["code"] == "7101":  # a carry-forward stock, not the flagged one
            item["factor"] = "勝手に書き換えた要因である。"
    _write_result(research_dir, entry, drifted)
    with pytest.raises(ResearchValidationError) as exc_info:
        compile_research_results(research_dir, strict=True)
    assert any("repair carry-forward drift" in error and "7101" in error
               for error in exc_info.value.errors)

    _write_result(research_dir, entry)  # verbatim carry-forward passes
    evidence, _ = compile_research_results(research_dir, strict=True)
    assert evidence["complete"] is True


def test_plan_main_refuses_replan_after_repair_without_force(tmp_path, capsys):
    research_dir, ranking, manifest = _setup(tmp_path)
    apply_repair_targets(research_dir, _payload(_target("7102")))
    ranking_path = tmp_path / "ranking.json"
    ranking_path.write_text(json.dumps(ranking, ensure_ascii=False), encoding="utf-8")
    manifest_bytes = (research_dir / "manifest.json").read_bytes()
    entry = _manifest(research_dir)["batches"][0]
    batch_bytes = (research_dir / entry["path"]).read_bytes()

    assert plan_main(["--ranking", str(ranking_path), "--out-dir", str(research_dir)]) == 1

    assert "repair適用済み" in capsys.readouterr().err
    assert (research_dir / "manifest.json").read_bytes() == manifest_bytes
    assert (research_dir / entry["path"]).read_bytes() == batch_bytes

    assert plan_main(
        ["--ranking", str(ranking_path), "--out-dir", str(research_dir), "--force"]
    ) == 0

    # --force rebuilds from scratch: repair state is discarded (attempts reset,
    # repair_context dropped) and the pre-repair checkpoint matches the rebuilt
    # digest again, so it is reused as complete.
    rebuilt = _manifest(research_dir)
    assert all("repair_attempts" not in item for item in rebuilt["batches"])
    assert "repair_context" not in _batch(research_dir, rebuilt["batches"][0])
    assert rebuilt["batches"][0]["status"] == "complete"
    assert rebuilt["input_digest"] == manifest["input_digest"]
