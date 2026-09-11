"""Cloud Bigtable MCP toolset factory.

The real-time cashier telemetry surface is served exclusively through the
Cloud Run MCP Toolbox microservice declared in ``tools.yaml``. The coordinator
agent mounts the MCP toolset returned by :func:`get_bigtable_mcp_toolset`; no
duplicate native Python client is maintained here (single source of truth).

The Cloud Run endpoint is resolved from the environment via :mod:`app.config`
and is never hardcoded.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams

from app import config

logger = logging.getLogger(__name__)

MCP_SSE_PATH = "/mcp/sse"
_TOKEN_TIMEOUT_SECONDS = 30


def _get_id_token() -> str:
    """Retrieve a GCP OIDC identity token for authenticating to Cloud Run.

    Returns an empty string when no credential source is available so the caller
    can fall back to unauthenticated local development endpoints.
    """
    gcloud = shutil.which("gcloud")
    if not gcloud:
        logger.warning("gcloud CLI not found on PATH; MCP calls will be unauthenticated.")
        return ""
    try:
        return (
            subprocess.check_output(
                [gcloud, "auth", "print-identity-token"],
                stderr=subprocess.DEVNULL,
                timeout=_TOKEN_TIMEOUT_SECONDS,
            )
            .decode("utf-8")
            .strip()
        )
    except Exception:
        # Blueprint Section 4.3: log internally, expose nothing.
        logger.exception("Failed to mint a Cloud Run OIDC identity token.")
        return ""


def get_bigtable_mcp_toolset() -> McpToolset:
    """Instantiate the ADK ``McpToolset`` bound to the Cloud Run Bigtable microservice.

    Returns:
        A configured :class:`McpToolset` exposing the ``get_cashier_realtime_metrics``
        Bigtable GoogleSQL tool declared in ``tools.yaml``.

    Raises:
        app.config.ConfigurationError: If ``BIGTABLE_MCP_URL`` is not configured.
    """
    base_url = config.get_bigtable_mcp_url()
    token = _get_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    logger.info("Binding Bigtable MCP toolset to configured Cloud Run endpoint.")
    return McpToolset(
        connection_params=SseConnectionParams(
            url=f"{base_url}{MCP_SSE_PATH}",
            headers=headers,
        )
    )
