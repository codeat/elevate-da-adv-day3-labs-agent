"""Analytics Tool wrapping BigQuery Conversational Data Agent."""

import json
import logging
import os
import time
import google.auth
import google.auth.transport.requests
import requests

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "panliuyang-ramp-up-project-01")
LOCATION = os.environ.get("LOCATION", "global")
DATA_AGENT_ID = os.environ.get("DATA_AGENT_ID", "cymbal-retail-analytics-agent")
DATA_AGENT_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/dataAgents/{DATA_AGENT_ID}"
CHAT_URL = f"https://geminidataanalytics.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}:chat"


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
        Structured string containing the generated SQL, retrieved tabular data, and analytical summary.
    """
    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            auth_req = google.auth.transport.requests.Request()
            creds.refresh(auth_req)

            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": PROJECT_ID,
            }
            payload = {
                "parent": f"projects/{PROJECT_ID}/locations/{LOCATION}",
                "dataAgentContext": {
                    "dataAgent": DATA_AGENT_NAME,
                },
                "messages": [
                    {
                        "userMessage": {
                            "text": query,
                        }
                    }
                ],
            }

            resp = requests.post(CHAT_URL, headers=headers, json=payload, timeout=90)
            if resp.status_code == 200:
                data = resp.json()
                generated_sql = None
                data_rows = []
                data_headers = []
                final_text = []

                for item in data:
                    if "systemMessage" in item:
                        sm = item["systemMessage"]
                        if "generatedSql" in sm:
                            generated_sql = sm["generatedSql"].get("query")
                        if "data" in sm:
                            schema = sm["data"].get("schema", {})
                            data_headers = [f["name"] for f in schema.get("fields", [])]
                            data_rows = sm["data"].get("rows", [])
                        if "text" in sm:
                            parts = sm["text"].get("parts", [])
                            for part in parts:
                                if isinstance(part, str):
                                    # Filter out intermediate engine logging
                                    if not any(part.startswith(p) for p in [
                                        "Analyzing context",
                                        "Context retrieved",
                                        "Retrieved context",
                                        "Running a query",
                                        "Query execution completed",
                                        "Query returned",
                                    ]):
                                        final_text.append(part)

                output_parts = []
                if generated_sql:
                    output_parts.append(f"### Generated GoogleSQL\n```sql\n{generated_sql.strip()}\n```")
                if data_rows and data_headers:
                    output_parts.append(f"### Data Output ({len(data_rows)} rows)")
                    header_line = "| " + " | ".join(data_headers) + " |"
                    sep_line = "| " + " | ".join(["---"] * len(data_headers)) + " |"
                    rows_lines = []
                    for r in data_rows[:20]:
                        row_vals = [str(v) if v is not None else "NULL" for v in r]
                        rows_lines.append("| " + " | ".join(row_vals) + " |")
                    output_parts.append("\n".join([header_line, sep_line] + rows_lines))
                if final_text:
                    output_parts.append("### Summary Analysis\n" + "\n\n".join(final_text))

                if not output_parts:
                    return f"Data Agent completed the query but returned no content. Response: {json.dumps(data)}"
                return "\n\n".join(output_parts)

            elif resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text}"
                time.sleep(2 ** attempt)
        except Exception as e:
            last_error = str(e)
            time.sleep(2 ** attempt)

    return (
        f"Store data service is currently unreachable. "
        f"Please verify network connectivity or check if the BigQuery Conversational Data Agent is active. "
        f"(Diagnostics: {last_error})"
    )
