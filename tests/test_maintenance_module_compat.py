"""maintenance 本体と scripts/ 互換CLIの接続を固定する。"""
import importlib
from pathlib import Path
import subprocess
import sys

from tse_ranking_monitor.maintenance import backfill, calendar_gate


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_maintenance_modules_reexport_implementation_symbols():
    for legacy_name, implementation in (
        ("backfill_pct5", backfill),
        ("check_gate", calendar_gate),
    ):
        legacy = importlib.import_module(legacy_name)
        for name in dir(implementation):
            if name.startswith("__"):
                continue
            assert hasattr(legacy, name), "%s does not re-export %s" % (legacy_name, name)
            assert getattr(legacy, name) is getattr(implementation, name)


def test_backfill_legacy_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backfill_pct5.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--data-dir" in result.stdout
    assert "--dry-run" in result.stdout


def test_calendar_gate_legacy_cli_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_gate.py"), "2026-01-01"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "SKIP"


def test_calendar_gate_legacy_cli_help():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_gate.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "YYYY-MM-DD" in result.stdout


def test_gmail_token_legacy_cli_help_does_not_prompt():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "get_gmail_token.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "OAuth" in result.stdout
    assert "GMAIL_CLIENT_ID を貼り付け" not in result.stdout
