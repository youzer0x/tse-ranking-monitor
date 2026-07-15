"""Compatibility facade for :mod:`tse_ranking_monitor.publishing.render`.

The implementation and web source assets live under ``src/``.  Existing routine
commands and flat imports from ``scripts/`` intentionally remain supported.
"""

from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tse_ranking_monitor.publishing import render as _implementation

globals().update({
    name: getattr(_implementation, name)
    for name in dir(_implementation)
    if not name.startswith("__")
})
