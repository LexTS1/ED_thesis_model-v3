"""Provenance helpers for scenario-tree run attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import subprocess
from typing import Any, Mapping


DEFAULT_HASH_SIZE_LIMIT_BYTES = 512 * 1024 * 1024


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(
    path: Path,
    *,
    max_size_bytes: int | None = DEFAULT_HASH_SIZE_LIMIT_BYTES,
) -> str:
    """Return a SHA-256 file digest, or an explicit skip marker for huge files."""

    resolved = Path(path)
    if not resolved.exists():
        return "missing_file"
    if not resolved.is_file():
        return "not_a_file"
    size = resolved.stat().st_size
    if max_size_bytes is not None and size > max_size_bytes:
        return f"hash_skipped_large_file:size_bytes={size}:limit_bytes={max_size_bytes}"

    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def get_git_commit(repo_root: Path) -> str | None:
    """Return the current git commit hash when available."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        return None
    return value


def get_git_dirty(repo_root: Path) -> bool | None:
    """Return whether the git working tree has uncommitted changes."""

    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def get_model_version(config: Mapping[str, Any] | None = None) -> str:
    """Return the most specific model version available."""

    config = dict(config or {})
    model_cfg = dict(config.get("model", {}))
    for value in (
        model_cfg.get("version"),
        model_cfg.get("model_version"),
        config.get("version"),
        config.get("model_version"),
    ):
        if value:
            return str(value)

    try:
        import model_v3
    except Exception:
        return "model_v3.unversioned"
    return str(getattr(model_v3, "__version__", "model_v3.unversioned"))

