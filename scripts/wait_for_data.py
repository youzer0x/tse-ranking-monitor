"""互換CLI: 実装本体は :mod:`tse_ranking_monitor.gate` にある。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tse_ranking_monitor import gate as _impl

business_day = _impl.business_day
jquants = _impl.jquants
JST = _impl.JST
DEFAULT_MANIFEST = _impl.DEFAULT_MANIFEST
evaluate = _impl.evaluate
resolve_deadline = _impl.resolve_deadline
emit_session_and_exit = _impl.emit_session_and_exit
read_published_dates = _impl.read_published_dates
latest_completed_session = _impl.latest_completed_session
select_target_session = _impl.select_target_session
main = _impl.main


if __name__ == "__main__":
    sys.exit(main())
