"""Repository-level package shim for the model_v3 scaffold.

This extends the package search path so imports like ``model_v3.interfaces``
resolve to the actual implementation package under ``model_v3/src/model_v3``.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]

_SRC_PACKAGE = Path(__file__).resolve().parent / "src" / "model_v3"
if _SRC_PACKAGE.exists():
    __path__.append(str(_SRC_PACKAGE))
