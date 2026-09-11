"""Shared pytest fixtures. All tests run fully offline (no GCP calls).

The synthetic environment is installed at *import* time - before any ``app.*``
module is loaded - because the production configuration layer deliberately
fails fast when mandatory properties are absent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SYNTHETIC_ENV = {
    "PROJECT_ID": "unit-test-project",
    "BIGTABLE_MCP_URL": "https://mcp.example.invalid",
    "LOCATION": "global",
    "REGION": "us-central1",
}
for _key, _value in _SYNTHETIC_ENV.items():
    os.environ.setdefault(_key, _value)

# Neither the MCP transport nor the Data Agent gateway may open a socket or
# resolve real credentials during unit tests.
from unittest.mock import MagicMock, patch  # noqa: E402

import google.auth.credentials  # noqa: E402

_FAKE_ADC = MagicMock(spec=google.auth.credentials.Credentials)

_MCP_PATCHERS = [
    patch("google.adk.tools.mcp_tool.mcp_toolset.McpToolset", MagicMock()),
    patch("google.adk.tools.data_agent.DataAgentToolset", MagicMock()),
    patch("google.auth.default", MagicMock(return_value=(_FAKE_ADC, "unit-test-project"))),
]
for _patcher in _MCP_PATCHERS:
    _patcher.start()

import pytest  # noqa: E402

_MANAGED_PREFIXES = (
    "PROJECT_ID",
    "GOOGLE_CLOUD_",
    "GCP_",
    "LOCATION",
    "REGION",
    "MODEL_NAME",
    "COORDINATOR_MODEL",
    "DATA_AGENT_ID",
    "BIGTABLE_",
    "MCP_SERVICE_URL",
    "RAG_",
    "BQ_MAX_BYTES_BILLED",
    "TOOL_MAX_RETRIES",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Reset every managed environment property to a deterministic baseline."""
    from app import config

    for key in list(os.environ):
        if key.startswith(_MANAGED_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    for key, value in _SYNTHETIC_ENV.items():
        monkeypatch.setenv(key, value)

    config.get_project_id.cache_clear()
    yield
    config.get_project_id.cache_clear()
