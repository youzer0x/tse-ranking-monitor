"""src 本体と scripts/ 互換モジュールの接続を固定する。"""
import importlib

from tse_ranking_monitor.market import assemble, brief, stats
from tse_ranking_monitor.quality import market, ranking


def test_legacy_modules_reexport_every_implementation_symbol():
    pairs = (
        ("build_market_stats", stats),
        ("build_market_json", assemble),
        ("build_market_brief", brief),
        ("validate_market_quality", market),
        ("validate_ranking_quality", ranking),
    )
    for legacy_name, implementation in pairs:
        legacy = importlib.import_module(legacy_name)
        for name in dir(implementation):
            if name.startswith("__"):
                continue
            assert hasattr(legacy, name), "%s does not re-export %s" % (legacy_name, name)
            assert getattr(legacy, name) is getattr(implementation, name)
