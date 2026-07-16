"""Coding Remote v1.2.0 · terminal Webbee Code remote-control tools — system extension."""
from __future__ import annotations

import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
for _m in [k for k in sys.modules if k in ("app", "handlers", "models", "panels")]:
    del sys.modules[_m]

from app import ext, chat  # noqa: F401
import handlers            # noqa: F401
import panels              # noqa: F401
