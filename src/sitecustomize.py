"""Process-wide Python startup customisation for local model_v3 runs."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _ensure_writable_matplotlib_config_dir() -> None:
    configured = os.environ.get("MPLCONFIGDIR")
    if configured:
        Path(configured).mkdir(parents=True, exist_ok=True)
        return

    cache_dir = Path(tempfile.gettempdir()) / "model_v3_matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(cache_dir)


_ensure_writable_matplotlib_config_dir()

