"""互換CLI: 実装本体は :mod:`tse_ranking_monitor.ranking` にある。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tse_ranking_monitor import ranking as _impl

jquants = _impl.jquants
kabutan_pts = _impl.kabutan_pts
tdnet = _impl.tdnet
business_day = _impl.business_day
mcap = _impl.mcap
annotate_sector_clusters = _impl.annotate_sector_clusters
validate_source_data = _impl.validate_source_data
validate_ranking_document = _impl.validate_ranking_document
build = _impl.build
main = _impl.main


if __name__ == "__main__":
    main()
