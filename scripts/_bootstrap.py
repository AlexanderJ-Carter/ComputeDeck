"""把仓库 ``lib/`` 插入 ``sys.path``，供 scripts 入口使用。"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
