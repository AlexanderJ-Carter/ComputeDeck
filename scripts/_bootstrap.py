"""Insert ``lib/`` onto ``sys.path`` for script entrypoints."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
