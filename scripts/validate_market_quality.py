#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""互換CLI: 本体は :mod:`tse_ranking_monitor.quality.market`。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tse_ranking_monitor.quality import market as _impl

globals().update({name: getattr(_impl, name) for name in dir(_impl)
                  if not name.startswith("__")})

if __name__ == "__main__":
    sys.exit(_impl.main())
