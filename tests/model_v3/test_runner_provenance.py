"""Tests for scenario-tree provenance helpers."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_V3_SRC = REPO_ROOT / "src"
if str(MODEL_V3_SRC) not in sys.path:
    sys.path.insert(0, str(MODEL_V3_SRC))

from model_v3.scenarios.provenance import (  # noqa: E402
    get_git_commit,
    get_git_dirty,
    get_model_version,
    sha256_file,
    utc_now_iso,
)


def test_sha256_file_records_config_hash(tmp_path: Path) -> None:
    path = tmp_path / "run_config.yaml"
    content = b"scenario_leaf:\n  id: test\n"
    path.write_bytes(content)

    assert sha256_file(path, max_size_bytes=None) == hashlib.sha256(content).hexdigest()


def test_sha256_file_reports_missing_and_large_skips(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    large = tmp_path / "large.bin"
    large.write_bytes(b"12345")

    assert sha256_file(missing) == "missing_file"
    assert sha256_file(large, max_size_bytes=4).startswith("hash_skipped_large_file:")


def test_git_provenance_returns_known_shape() -> None:
    commit = get_git_commit(REPO_ROOT)
    dirty = get_git_dirty(REPO_ROOT)

    assert commit is None or len(commit) >= 7
    assert dirty in {True, False, None}


def test_model_version_and_utc_timestamp_are_recordable() -> None:
    assert get_model_version()
    assert utc_now_iso().endswith("Z")

