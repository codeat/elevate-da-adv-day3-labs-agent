"""Regression guard: fail the build if any tenant-specific literal reappears."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".tf"}
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "build", ".pytest_cache", ".ruff_cache", "tests"}

# Any *.run.app endpoint or a bare `<something>-project-<digits>` tenant id.
CLOUD_RUN_LITERAL = re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]*\.?run\.app")
TENANT_PROJECT_LITERAL = re.compile(r"\b[a-z][a-z0-9-]*-ramp-up-project-\d+\b")


def _scanned_files():
    for path in REPO_ROOT.rglob("*"):
        if path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


@pytest.mark.parametrize("pattern", [CLOUD_RUN_LITERAL, TENANT_PROJECT_LITERAL])
def test_no_hardcoded_environment_literals(pattern):
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(text):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)}")
    assert not offenders, (
        "Hardcoded environment-specific literals detected. Move them to "
        "environment variables (see app/config.py):\n  " + "\n  ".join(offenders)
    )
