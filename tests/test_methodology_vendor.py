"""Methodology snapshot lock tests."""

import json

import check_methodology_vendor as checker


def _install_snapshot(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    skill = vendor / "SKILL.md"
    skill.write_text("methodology\n", encoding="utf-8")
    lock = vendor / "vendor.lock.json"
    lock.write_text(json.dumps({
        "source": "example/methodology",
        "commit": "a" * 40,
        "files": {"SKILL.md": checker.normalized_sha256(skill)},
    }), encoding="utf-8")
    monkeypatch.setattr(checker, "VENDOR", vendor)
    monkeypatch.setattr(checker, "LOCK", lock)
    return vendor


def test_snapshot_lock_accepts_exact_file_set(tmp_path, monkeypatch):
    _install_snapshot(tmp_path, monkeypatch)
    assert checker.main() == 0


def test_snapshot_lock_rejects_unlocked_file(tmp_path, monkeypatch):
    vendor = _install_snapshot(tmp_path, monkeypatch)
    (vendor / "unexpected.md").write_text("drift", encoding="utf-8")
    assert checker.main() == 1
