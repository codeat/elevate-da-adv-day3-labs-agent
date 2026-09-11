"""Enterprise analytics gateway backed by the BigQuery Conversational Data Agent.

Per the system design document this gateway is built on ADK's **native**
``DataAgentToolset`` / ``ask_data_agent`` integration rather than hand-rolled
HTTP calls, so that ADK owns credential refresh, retry semantics, tool-context
propagation, and result-row capping.

A thin REST adapter is retained ONLY as a degraded fallback for runtimes where
the native toolset cannot be constructed (e.g. an older ADK release). The
native path is always preferred.

All environment-specific values resolve through :mod:`app.config`; failure
paths emit the sanitized contracts in :mod:`app.contracts`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from google.adk.tools.data_agent import (
    DataAgentCredentialsConfig,
    DataAgentToolConfig,
    DataAgentToolset,
)

from app import config
from app.contracts import (
    ANALYTICS_EMPTY_RESULT_RESPONSE,
    ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE,
)

logger = logging.getLogger(__name__)

DATA_AGENT_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
MAX_QUERY_RESULT_ROWS = 50
REQUEST_TIMEOUT_SECONDS = 90
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Intermediate engine progress chatter that must not reach the transcript.
_ENGINE_LOG_PREFIXES = (
    "Analyzing context",
    "Context retrieved",
    "Retrieved context",
    "Running a query",
    "Query execution completed",
    "Query returned",
)


# ---------------------------------------------------------------------------
# Native ADK toolset (primary integration path)
# ---------------------------------------------------------------------------
def _resolve_adc_credentials():
    """Resolve Application Default Credentials for the analytics gateway.

    No service-account key material is ever read from the repository; the
    runtime identity is supplied by the platform (Cloud Run service account or
    locally-attached ADC). See ``docs/design_blueprint.md`` Section 4.2.
    """
    import google.auth

    credentials, _ = google.auth.default(scopes=DATA_AGENT_SCOPES)
    return credentials


def get_data_agent_toolset() -> DataAgentToolset:
    """Build the ADK-native Conversational Data Agent toolset.

    Exposes ``ask_data_agent`` (read-only) so the coordinator delegates NL2SQL
    analytics through ADK's first-party integration instead of raw REST.

    Returns:
        A read-only :class:`DataAgentToolset` filtered to ``ask_data_agent``.
    """
    tool_config = DataAgentToolConfig(
        max_query_result_rows=MAX_QUERY_RESULT_ROWS,
        location=config.get_location(),
        # Read-only posture: the agent may never create/update/delete agents.
        enable_data_agent_modification=False,
    )
    # `credentials` is mutually exclusive with `scopes` in ADK's credential
    # model, so scopes are bound while resolving ADC instead.
    credentials_config = DataAgentCredentialsConfig(credentials=_resolve_adc_credentials())
    logger.info(
        "Binding ADK-native DataAgentToolset (location=%s, max_rows=%d).",
        config.get_location(),
        MAX_QUERY_RESULT_ROWS,
    )
    return DataAgentToolset(
        tool_filter=["ask_data_agent"],
        credentials_config=credentials_config,
        data_agent_tool_config=tool_config,
    )


# ---------------------------------------------------------------------------
# Shared response rendering
# ---------------------------------------------------------------------------
def _render_markdown_table(headers: list[str], rows: list[list], limit: int = 20) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = [
        "| " + " | ".join(str(v) if v is not None else "NULL" for v in row) + " |"
        for row in rows[:limit]
    ]
    return "\n".join([header_line, sep_line] + body)


def _render_data_agent_response(steps: list[dict[str, Any]]) -> str:
    """Render an ``ask_data_agent`` / REST response envelope into Markdown.

    Handles both the native ADK shape (``{"data": {...}}`` / ``{"text": {...}}``)
    and the raw REST shape (``{"systemMessage": {...}}``).
    """
    generated_sql: str | None = None
    data_headers: list[str] = []
    data_rows: list = []
    final_text: list[str] = []

    for step in steps:
        # Native ADK envelope nests under `data` / `text`; REST nests under
        # `systemMessage`. Normalize both before extraction.
        payload = step.get("systemMessage", step)

        data_block = payload.get("data")
        if isinstance(data_block, dict):
            if "generatedSql" in data_block:
                sql = data_block["generatedSql"]
                generated_sql = sql.get("query") if isinstance(sql, dict) else sql
            schema = data_block.get("schema", {})
            if schema:
                data_headers = [f["name"] for f in schema.get("fields", [])]
            if data_block.get("rows"):
                data_rows = data_block["rows"]

        if "generatedSql" in payload and generated_sql is None:
            sql = payload["generatedSql"]
            generated_sql = sql.get("query") if isinstance(sql, dict) else sql

        text_block = payload.get("text")
        if isinstance(text_block, dict):
            # THOUGHT blocks are internal reasoning - never surface them.
            if text_block.get("textType") == "THOUGHT":
                continue
            for part in text_block.get("parts", []):
                if isinstance(part, str) and not part.startswith(_ENGINE_LOG_PREFIXES):
                    final_text.append(part)

    output_parts: list[str] = []
    if generated_sql:
        output_parts.append(f"### Generated GoogleSQL\n```sql\n{generated_sql.strip()}\n```")
    if data_rows and data_headers:
        output_parts.append(f"### Data Output ({len(data_rows)} rows)")
        output_parts.append(_render_markdown_table(data_headers, data_rows))
    if final_text:
        output_parts.append("### Summary Analysis\n" + "\n\n".join(final_text))

    return "\n\n".join(output_parts)


# ---------------------------------------------------------------------------
# Degraded REST adapter (fallback only)
# ---------------------------------------------------------------------------
def cymbal_analytics_tool(query: str) -> str:
    """Query enterprise retail data and analytics using the BigQuery Conversational Data Agent.

    Prefer the ADK-native ``ask_data_agent`` tool exposed by
    :func:`get_data_agent_toolset`. This function is the degraded REST adapter
    retained for runtimes where the native toolset is unavailable.

    Use this tool for:
    - Intraday & historical POS transaction analysis (e.g. Net Transaction Revenue, discount amounts, item quantities).
    - Store inventory positions, on-hand stock, and stockout risk (e.g. Total On-Hand Inventory, Estimated Cover Hours).
    - Warranty claims, terms, and purchase policy triage (e.g. warranty coverage for transaction line items).
    - Cashier anomaly analysis and 7-day historical baselines (e.g. Cashier Manual Override Rate, promo abuse rankings).
    - Cross-cloud federated AWS S3 transaction audits (e.g. historical AWS checkout logs).

    Args:
        query: The natural language question to ask the Data Agent. Preserve business terms
            (such as 'Estimated Cover Hours', 'Total On-Hand Inventory', 'Net Transaction Revenue',
            'Cashier Manual Override Rate') verbatim without stripping or summarization.

    Returns:
        Structured string containing the generated SQL, retrieved tabular data, and
        analytical summary. On persistent backend unavailability a sanitized fallback
        notice is returned without internal diagnostics.
    """
    import google.auth
    import google.auth.transport.requests
    import requests

    project_id = config.get_project_id()
    location = config.get_location()
    chat_url = config.get_chat_url()
    max_retries = config.get_max_retries()

    payload = {
        "parent": f"projects/{project_id}/locations/{location}",
        "dataAgentContext": {"dataAgent": config.get_data_agent_name()},
        "messages": [{"userMessage": {"text": query}}],
    }

    for attempt in range(1, max_retries + 1):
        try:
            creds, _ = google.auth.default(scopes=DATA_AGENT_SCOPES)
            creds.refresh(google.auth.transport.requests.Request())

            resp = requests.post(
                chat_url,
                headers={
                    "Authorization": f"Bearer {creds.token}",
                    "Content-Type": "application/json",
                    "X-Goog-User-Project": project_id,
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if resp.status_code in RETRYABLE_STATUS:
                logger.warning(
                    "Data Agent returned retryable status %s (attempt %d/%d).",
                    resp.status_code,
                    attempt,
                    max_retries,
                )
                if attempt < max_retries:
                    time.sleep(2**attempt)
                continue

            if resp.status_code != 200:
                # Body may embed internal resource names -> log only.
                logger.error(
                    "Data Agent call failed with HTTP %s. Body: %s",
                    resp.status_code,
                    resp.text,
                )
                if attempt < max_retries:
                    time.sleep(2**attempt)
                continue

            rendered = _render_data_agent_response(resp.json())
            if not rendered:
                logger.info("Data Agent returned an empty payload for query: %s", query)
                return ANALYTICS_EMPTY_RESULT_RESPONSE
            return rendered

        except Exception:
            # Blueprint Section 4.3: never surface stack traces to the caller.
            logger.exception(
                "Analytics tool attempt %d/%d raised an unexpected error.",
                attempt,
                max_retries,
            )
            if attempt < max_retries:
                time.sleep(2**attempt)

    return ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE
