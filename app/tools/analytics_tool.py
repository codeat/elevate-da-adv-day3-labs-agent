"""Analytics tool wrapping the BigQuery Conversational Data Agent.

All environment-specific values (project, location, data agent id, endpoint)
are resolved from :mod:`app.config`. Failure paths emit the sanitized contract
defined in :mod:`app.contracts`; raw exceptions and HTTP bodies are logged
server-side only (Blueprint Section 4.3).
"""

from __future__ import annotations

import logging
import time

import google.auth
import google.auth.transport.requests
import requests

from app import config
from app.contracts import (
    ANALYTICS_EMPTY_RESULT_RESPONSE,
    ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT_SECONDS = 90

# Intermediate engine progress chatter that must not reach the transcript.
_ENGINE_LOG_PREFIXES = (
    "Analyzing context",
    "Context retrieved",
    "Retrieved context",
    "Running a query",
    "Query execution completed",
    "Query returned",
)


def _render_markdown_table(headers: list[str], rows: list[list], limit: int = 20) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = [
        "| " + " | ".join(str(v) if v is not None else "NULL" for v in row) + " |"
        for row in rows[:limit]
    ]
    return "\n".join([header_line, sep_line] + body)


def cymbal_analytics_tool(query: str) -> str:
    """Query enterprise retail data and analytics using the BigQuery Conversational Data Agent.

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
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(google.auth.transport.requests.Request())

            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": project_id,
            }

            resp = requests.post(
                chat_url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
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

            data = resp.json()
            generated_sql: str | None = None
            data_rows: list = []
            data_headers: list[str] = []
            final_text: list[str] = []

            for item in data:
                system_message = item.get("systemMessage")
                if not system_message:
                    continue
                if "generatedSql" in system_message:
                    generated_sql = system_message["generatedSql"].get("query")
                if "data" in system_message:
                    schema = system_message["data"].get("schema", {})
                    data_headers = [f["name"] for f in schema.get("fields", [])]
                    data_rows = system_message["data"].get("rows", [])
                if "text" in system_message:
                    for part in system_message["text"].get("parts", []):
                        if isinstance(part, str) and not part.startswith(_ENGINE_LOG_PREFIXES):
                            final_text.append(part)

            output_parts: list[str] = []
            if generated_sql:
                output_parts.append(
                    f"### Generated GoogleSQL\n```sql\n{generated_sql.strip()}\n```"
                )
            if data_rows and data_headers:
                output_parts.append(f"### Data Output ({len(data_rows)} rows)")
                output_parts.append(_render_markdown_table(data_headers, data_rows))
            if final_text:
                output_parts.append("### Summary Analysis\n" + "\n\n".join(final_text))

            if not output_parts:
                logger.info("Data Agent returned an empty payload for query: %s", query)
                return ANALYTICS_EMPTY_RESULT_RESPONSE
            return "\n\n".join(output_parts)

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
