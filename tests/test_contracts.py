"""Shared ranking contract tests."""

import pytest

from tse_ranking_monitor.contracts import validate_ranking_document


def _document(max_rank=30):
    return {
        "schema_version": 1,
        "session_date": "2026-07-15",
        "criteria": {"max_rank": max_rank},
        "capped": False,
        "counts": {
            "qualifying": 1,
            "ranked": 1,
            "dropped_turnover": 0,
            "dropped_mcap": 0,
        },
        "rows": [{
            "rank": 1,
            "code": "7000",
            "factor": "材料を確認",
            "factor_kind": "報道",
        }],
        "dropped_turnover": [],
        "dropped_mcap": [],
    }


def test_contract_accepts_stage1_and_publish_requirements():
    data = _document()
    assert validate_ranking_document(
        data, require_factors=True, require_stage1_counts=True
    ) is data


def test_contract_preserves_unlimited_ranking_compatibility():
    assert validate_ranking_document(_document(max_rank=None))


def test_contract_rejects_missing_factor_at_publish_boundary():
    data = _document()
    data["rows"][0]["factor"] = ""
    with pytest.raises(ValueError, match="factor is required"):
        validate_ranking_document(data, require_factors=True)


@pytest.mark.parametrize("mutate", [
    lambda d: d["rows"][0].update(pct=float("nan")),
    lambda d: d["rows"][0].update(turnover_m="bad"),
    lambda d: d["rows"][0].update(mcap_oku_exact=99.96),
    lambda d: d["rows"][0].update(mcap_date="2026-07-16"),
    lambda d: d["rows"][0].update(rank=True),
    lambda d: d["rows"][0].update(factor={"text": "unexpected"}),
    lambda d: d.update(criteria="bad"),
    lambda d: d["criteria"].update(max_rank="bad"),
])
def test_publication_contract_rejects_invalid_types_values_and_dates(mutate):
    from test_publish import _ranking
    data = _ranking()
    data["schema_version"] = 1
    mutate(data)
    with pytest.raises(ValueError):
        validate_ranking_document(data, require_factors=True, require_numeric_fields=True)
