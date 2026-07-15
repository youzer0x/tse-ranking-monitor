"""Compatibility entry point for the one-off Gmail OAuth token utility."""

from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tse_ranking_monitor.maintenance import gmail_token as _implementation

globals().update({
    name: getattr(_implementation, name)
    for name in dir(_implementation)
    if not name.startswith("__")
})

if __name__ == "__main__":
    _implementation.main()
