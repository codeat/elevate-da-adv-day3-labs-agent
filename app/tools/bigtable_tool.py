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
        A configured :class:`McpToolset` exposing the Bigtable GoogleSQL tools
        declared in ``tools.yaml``: ``read_cashier_realtime_alerts_sql`` and
        ``read_pos_transactions_enriched_sql``.

    Note:
        The tool identifiers are owned by ``tools.yaml`` and materialise only
        when that manifest is published to Secret Manager and the Cloud Run
        revision is rolled (``make mcp-deploy``). Editing ``tools.yaml`` alone
        leaves the previous revision serving the previous names, and the agent
        then silently degrades: it cannot find the tool the system instruction
        promises and falls back to whatever gateway is left. Verify with::

            curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
                 -H "Content-Type: application/json" \
                 -H "Accept: application/json, text/event-stream" \
                 -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
                 "$BIGTABLE_MCP_URL/mcp"

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
