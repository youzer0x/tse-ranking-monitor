"""Compact unattended-routine contract lock tests."""

import json
from pathlib import Path

import runtime_contract as contract_cli
from tse_ranking_monitor.runtime import contract


def _write_sources(root: Path):
    for index, name in enumerate(contract.LOCKED_SOURCES):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source %d\r\n" % index, encoding="utf-8")


def test_contract_lock_hashes_compact_document_and_all_normative_sources(tmp_path):
    _write_sources(tmp_path)

    target = contract.write_contract_lock(tmp_path)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert list(payload["files"]) == list(contract.LOCKED_SOURCES)
    assert contract.verify_contract_lock(tmp_path) == []


def test_contract_check_fails_closed_when_compact_contract_changes(tmp_path):
    _write_sources(tmp_path)
    contract.write_contract_lock(tmp_path)
    (tmp_path / contract.CONTRACT_DOCUMENT).write_text(
        "changed execution contract\n", encoding="utf-8"
    )

    failures = contract.verify_contract_lock(tmp_path)

    assert any(
        failure.startswith("modified source: %s" % contract.CONTRACT_DOCUMENT)
        for failure in failures
    )


def test_contract_check_fails_closed_on_missing_lock(tmp_path):
    _write_sources(tmp_path)

    failures = contract.verify_contract_lock(tmp_path)

    assert failures and "could not be read" in failures[0]


def test_cli_accepts_documented_contract_option(tmp_path, capsys):
    _write_sources(tmp_path)
    contract.write_contract_lock(tmp_path, "custom.lock.json")

    result = contract_cli.main([
        "check", "--root", str(tmp_path), "--contract", "custom.lock.json"
    ])

    assert result == 0
    assert capsys.readouterr().out.startswith("OK:")
