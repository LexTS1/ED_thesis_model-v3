"""Matplotlib runtime configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def ensure_writable_matplotlib_config_dir() -> str:
    """Set a repo-independent Matplotlib config/cache directory if none is configured."""

    configured = os.environ.get("MPLCONFIGDIR")
    if configured:
        Path(configured).mkdir(parents=True, exist_ok=True)
        return configured

    cache_dir = Path(tempfile.gettempdir()) / "model_v3_matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(cache_dir)
    return str(cache_dir)

