"""Dispatch reservation ledger tests (reserve_dispatch CLI core)."""

import json
import threading

from tse_ranking_monitor.research.ledger import main as ledger_main
from tse_ranking_monitor.research.plan import write_research_plan


def _row(code, rank):
    return {
        "code": code,
        "name": f"Company {code}",
        "rank": rank,
        "pct": 20.0,  # large_move -> high risk -> batches of 3
        "pct5": 3.0,
        "turnover_m": 500.0,
        "disclosures": [],
        "kabutan_news": [],
    }


def _research_dir(tmp_path, rows=12):
    research_dir = tmp_path / "research"
    ranking = {
        "session_date": "2026-07-15",
        "prev_date": "2026-07-14",
        "rows": [_row(f"{7000 + index}", index) for index in range(1, rows + 1)],
        "theme_clusters": [],
    }
    write_research_plan(ranking, research_dir)
    return research_dir


def _reserve(research_dir, batch_id):
    return ledger_main(["--research-dir", str(research_dir), "--batch", batch_id])


def _manifest(research_dir):
    return json.loads((research_dir / "manifest.json").read_text(encoding="utf-8"))


def test_reserve_on_pending_batch_updates_ledger_on_disk(tmp_path):
    research_dir = _research_dir(tmp_path)

    assert _reserve(research_dir, "batch-001") == 0

    manifest = _manifest(research_dir)
    assert manifest["ledger"] == {"reservations": {"batch-001": 1}, "total_reserved": 1}


def test_per_batch_cap_exhausts_with_exit_3_and_no_write(tmp_path):
    research_dir = _research_dir(tmp_path)
    for _ in range(3):
        assert _reserve(research_dir, "batch-001") == 0
    before = (research_dir / "manifest.json").read_bytes()

    assert _reserve(research_dir, "batch-001") == 3

    assert (research_dir / "manifest.json").read_bytes() == before
    assert _manifest(research_dir)["ledger"] == {
        "reservations": {"batch-001": 3},
        "total_reserved": 3,
    }


def test_total_cap_from_manifest_budget_exhausts_with_exit_3(tmp_path):
    research_dir = _research_dir(tmp_path)
    manifest_path = research_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dispatch_budget"]["total_limit"] = 2
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert _reserve(research_dir, "batch-001") == 0
    assert _reserve(research_dir, "batch-002") == 0
    assert _reserve(research_dir, "batch-003") == 3

    ledger = _manifest(research_dir)["ledger"]
    assert ledger["total_reserved"] == 2
    assert "batch-003" not in ledger["reservations"]


def test_unknown_or_non_pending_batch_is_misuse_exit_4(tmp_path):
    research_dir = _research_dir(tmp_path)

    assert _reserve(research_dir, "batch-999") == 4

    manifest_path = research_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["batches"][0]["status"] = "complete"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert _reserve(research_dir, manifest["batches"][0]["batch_id"]) == 4
    assert _manifest(research_dir)["ledger"] == {"reservations": {}, "total_reserved": 0}


def test_missing_manifest_is_io_error_exit_1(tmp_path):
    assert _reserve(tmp_path, "batch-001") == 1


def test_concurrent_reserves_serialize_without_lost_updates(tmp_path):
    research_dir = _research_dir(tmp_path, rows=18)  # 6 pending batches
    batch_ids = [f"batch-{index:03d}" for index in range(1, 7)]
    codes = [None] * len(batch_ids)

    def worker(index):
        codes[index] = _reserve(research_dir, batch_ids[index])

    threads = [
        threading.Thread(target=worker, args=(index,))
        for index in range(len(batch_ids))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    manifest = _manifest(research_dir)  # must parse after concurrent rewrites
    assert codes.count(0) == 6
    assert manifest["ledger"]["total_reserved"] == 6
    assert manifest["ledger"]["reservations"] == {batch_id: 1 for batch_id in batch_ids}
