"""Stage2 evidence compiler and legacy factors contract tests."""

import copy
import json

import pytest

from merge_factors import merge
from tse_ranking_monitor.research.evidence import (
    ResearchValidationError,
    compile_research_results,
    write_compiled_results,
)
from tse_ranking_monitor.research.plan import write_research_plan


CHECKS = {
    "disclosures": "done",
    "kabutan_news": "done",
    "web_search": "done",
    "sector_cluster": "na",
    "edinet": "na",
}


def _ranking():
    return {
        "session_date": "2026-07-15",
        "prev_date": "2026-07-14",
        "rows": [
            {
                "code": "6001",
                "name": "A",
                "rank": 1,
                "pct": 10.0,
                "pct5": 11.0,
                "turnover_m": 1000.0,
                "disclosures": [],
                "kabutan_news": [],
                "factor": "",
                "factor_kind": "",
            },
            {
                "code": "6002",
                "name": "B",
                "rank": 2,
                "pct": 8.0,
                "pct5": 9.0,
                "turnover_m": 800.0,
                "disclosures": [],
                "kabutan_news": [],
                "factor": "",
                "factor_kind": "",
            },
        ],
        "theme_clusters": [],
    }


def _theme_result(code):
    return {
        "code": code,
        "status": "complete",
        "confidence": "medium",
        "factor": "半導体関連株の上昇と並走したとみられる。",
        "factor_kind": "テーマ",
        "claims": [{"text": "同テーマの物色である", "source_ids": []}],
        "sources": [],
        "checks": dict(CHECKS),
        "market_note": "半導体関連の物色と並走。",
    }


def _write_results(research_dir, manifest, mutate=None):
    for entry in manifest["batches"]:
        items = [_theme_result(code) for code in entry["codes"]]
        result = {
            "schema_version": "research_batch_result.v1",
            "batch_id": entry["batch_id"],
            "input_digest": entry["input_digest"],
            "items": items,
        }
        if mutate:
            mutate(entry, result)
        path = research_dir / entry["result_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def test_compile_strict_emits_complete_evidence_and_merge_compatible_factors(tmp_path):
    research_dir = tmp_path / "research"
    ranking = _ranking()
    manifest = write_research_plan(ranking, research_dir)
    _write_results(research_dir, manifest)

    evidence, factors = write_compiled_results(research_dir, strict=True)

    assert evidence["schema_version"] == "evidence.v1"
    assert evidence["complete"] is True
    assert evidence["missing_codes"] == []
    assert evidence["errors"] == []
    assert [item["code"] for item in evidence["items"]] == ["6001", "6002"]
    assert all(set(item["checks"]) == set(CHECKS) for item in evidence["items"])
    assert json.loads((research_dir / "evidence.json").read_text(encoding="utf-8")) == evidence
    assert json.loads((research_dir / "factors.json").read_text(encoding="utf-8")) == factors

    merged_ranking = copy.deepcopy(ranking)
    merged, rejected, missing = merge(merged_ranking, factors)
    assert merged == ["6001", "6002"]
    assert rejected == []
    assert missing == []
    assert all(row["factor"] for row in merged_ranking["rows"])


def test_strict_missing_result_preserves_existing_outputs(tmp_path):
    research_dir = tmp_path / "research"
    write_research_plan(_ranking(), research_dir)
    evidence_path = research_dir / "evidence.json"
    factors_path = research_dir / "factors.json"
    evidence_path.write_text("old evidence", encoding="utf-8")
    factors_path.write_text("old factors", encoding="utf-8")

    with pytest.raises(ResearchValidationError, match="result .* is missing"):
        write_compiled_results(research_dir, strict=True)

    assert evidence_path.read_text(encoding="utf-8") == "old evidence"
    assert factors_path.read_text(encoding="utf-8") == "old factors"


def test_non_strict_reports_invalid_and_missing_codes(tmp_path):
    research_dir = tmp_path / "research"
    manifest = write_research_plan(_ranking(), research_dir)

    def duplicate_code(_entry, result):
        result["items"].append(copy.deepcopy(result["items"][0]))

    _write_results(research_dir, manifest, mutate=duplicate_code)
    evidence, factors = compile_research_results(research_dir, strict=False)

    assert evidence["complete"] is False
    assert any("duplicate code" in error for error in evidence["errors"])
    # The first unique item remains usable in non-strict diagnostic mode.
    assert len(factors) == 2


def test_strict_rejects_invalid_sources_checks_and_stale_digest(tmp_path):
    research_dir = tmp_path / "research"
    manifest = write_research_plan(_ranking(), research_dir)

    def invalid(_entry, result):
        first = result["items"][0]
        first["factor_kind"] = "報道"
        first["sources"] = [{
            "id": "s1",
            "label": "記事",
            "url": "not-a-url",
            "source_type": "article",
            "published_at": "2026-07-15T09:00:00+09:00",
            "window": "material",
        }]
        first["claims"][0]["source_ids"] = ["s1"]
        first["checks"].pop("edinet")

    _write_results(research_dir, manifest, mutate=invalid)
    with pytest.raises(ResearchValidationError) as exc_info:
        compile_research_results(research_dir, strict=True)
    assert "url must be http(s)" in str(exc_info.value)

    # A stale result cannot be silently reused after the input batch changes.
    first_entry = manifest["batches"][0]
    path = research_dir / first_entry["result_path"]
    result = json.loads(path.read_text(encoding="utf-8"))
    result["input_digest"] = "0" * 64
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ResearchValidationError, match="stale"):
        compile_research_results(research_dir, strict=True)


def test_unresolved_requires_theme_and_all_checks(tmp_path):
    research_dir = tmp_path / "research"
    manifest = write_research_plan(_ranking(), research_dir)

    def unresolved_report(_entry, result):
        result["items"][0].update({
            "status": "unresolved",
            "factor_kind": "報道",
            "sources": [{
                "id": "s1",
                "label": "記事",
                "url": "https://example.com/article",
                "source_type": "article",
                "published_at": "2026-07-15T09:00:00+09:00",
                "window": "material",
            }],
            "claims": [{"text": "未解決", "source_ids": ["s1"]}],
        })

    _write_results(research_dir, manifest, mutate=unresolved_report)
    with pytest.raises(ResearchValidationError, match="unresolved.*テーマ"):
        compile_research_results(research_dir, strict=True)
