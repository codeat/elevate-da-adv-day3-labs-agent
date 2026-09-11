"""Gating for the online integration layer.

These tests are DELIBERATELY excluded from the default ``pytest`` run
(``testpaths = ["tests/unit"]``) and additionally guarded by an opt-in
environment switch, so that CI on a fork - or a developer without ADC - never
sees a spurious red build.

Enable with::

    export RUN_INTEGRATION_TESTS=1
    export PROJECT_ID=<your-project>
    export BIGTABLE_MCP_URL=https://<cloud-run-host>
    make test-integration
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RUN_SWITCH = "RUN_INTEGRATION_TESTS"


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if os.environ.get(RUN_SWITCH):
        return
    skip = pytest.mark.skip(
        reason=f"Online integration tests are opt-in; export {RUN_SWITCH}=1 to enable."
    )
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def project_id() -> str:
    from app import config

    return config.get_project_id()


@pytest.fixture(scope="session")
def bq_client(project_id):
    bigquery = pytest.importorskip("google.cloud.bigquery")
    return bigquery.Client(project=project_id)


@pytest.fixture(scope="session")
def id_token_factory():
    """Mint a Cloud Run OIDC identity token for the MCP Toolbox audience."""

    def _mint(audience: str) -> str:
        import google.auth.transport.requests
        from google.oauth2 import id_token as google_id_token

        return google_id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)

    return _mint
