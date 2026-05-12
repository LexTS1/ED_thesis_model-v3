"""Compatibility package for running ``python3 -m model_v3...`` from the repo root.

The importable implementation lives under ``src/model_v3`` while the top-level
``model_v3`` directory holds experiment artifacts. Extending ``__path__`` keeps
the documented module entrypoints stable without moving the artifact tree.
"""

from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
_SRC_PACKAGE = _SRC_ROOT / "model_v3"
if _SRC_ROOT.exists():
    _src_root_text = str(_SRC_ROOT)
    if _src_root_text not in sys.path:
        sys.path.insert(0, _src_root_text)
if _SRC_PACKAGE.exists():
    _src_package_text = str(_SRC_PACKAGE)
    if _src_package_text not in __path__:
        __path__.append(_src_package_text)
_repo_root_text = str(_REPO_ROOT)
if _repo_root_text not in __path__:
    __path__.append(_repo_root_text)

try:
    from .utils.matplotlib_config import ensure_writable_matplotlib_config_dir

    ensure_writable_matplotlib_config_dir()
except Exception:
    # Keep the compatibility shim importable for orchestration-only commands.
    pass
