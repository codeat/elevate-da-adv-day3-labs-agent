"""Bigtable MCP Toolset and Real-Time Cashier Metrics Tool."""

import json
import logging
import os
import subprocess
import time
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "panliuyang-ramp-up-project-01")
MCP_SERVICE_URL = os.environ.get(
    "BIGTABLE_MCP_URL",
    "https://mcp-toolbox-bigtable-367960516524.us-central1.run.app"
)


def _get_id_token() -> str:
    """Retrieve GCP OIDC identity token for authenticating to Cloud Run."""
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"]
        ).decode("utf-8").strip()
        return token
    except Exception as e:
        logger.warning("Failed to get gcloud identity token: %s", e)
        return ""


def get_bigtable_mcp_toolset() -> McpToolset:
    """Instantiate the ADK McpToolset connecting to the Cloud Run Bigtable microservice."""
    token = _get_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return McpToolset(
        connection_params=SseConnectionParams(
            url=f"{MCP_SERVICE_URL}/mcp/sse",
            headers=headers
        )
    )


def get_cashier_realtime_metrics(prefix: str) -> str:
    """Query live 1-hour rolling metrics and audit status flags for a cashier from Cloud Bigtable.

    Use this tool when you need:
    - Real-time cashier metrics (e.g. 1-hour rolling metrics for Cashier CASH_1190 at Store 48).
    - Live audit status flags ('clear', 'review', 'flagged').
    - Live 1-hour manual override count, promo rate, transaction count, and average discount percentage.
    - Live 1-hour override rate to compare against 7-day historical baselines.

    Args:
        prefix: Cashier row key prefix (e.g. 'STORE_048#CASH_1190' or 'STORE_001#CASH_1001' or cashier ID 'CASH_1190').

    Returns:
        Structured string containing the live metrics, audit status, and calculated override rate.
    """
    # Clean prefix
    clean_prefix = prefix.strip()
    if not clean_prefix.startswith("STORE_") and "#" not in clean_prefix:
        # e.g., if just CASH_1190 or 1190
        if not clean_prefix.startswith("CASH_"):
            clean_prefix = f"CASH_{clean_prefix}"

    # Try querying via Bigtable GoogleSQL directly for maximum reliability and exact format
    try:
        from google.cloud.bigtable.data import BigtableDataClient

        client = BigtableDataClient(project=PROJECT_ID)
        query = """
        SELECT
          CAST(_key AS STRING) as row_key,
          CAST(flags['audit_status'] AS STRING) as audit_status,
          CAST(stats['last_event_ts'] AS STRING) as last_event_ts,
          TO_FLOAT64(stats['cashier_1h_avg_discount_pct']) as avg_discount_pct,
          TO_INT64(stats['cashier_1h_manual_override_count']) as manual_override_count,
          TO_INT64(stats['cashier_1h_promo_count']) as promo_count,
          TO_FLOAT64(stats['cashier_1h_promo_rate']) as promo_rate,
          TO_FLOAT64(stats['cashier_1h_total_discount_usd']) as total_discount_usd,
          TO_INT64(stats['cashier_1h_txn_count']) as txn_count,
          TO_FLOAT64(stats['risk_score']) as risk_score,
          CASE
            WHEN TO_INT64(stats['cashier_1h_txn_count']) > 0
            THEN ROUND(CAST(TO_INT64(stats['cashier_1h_manual_override_count']) AS FLOAT64) / CAST(TO_INT64(stats['cashier_1h_txn_count']) AS FLOAT64), 4)
            ELSE 0.0
          END as live_override_rate
        FROM cashier_realtime_alerts
        WHERE CAST(_key AS STRING) LIKE CONCAT('%', @prefix, '%')
        ORDER BY _key ASC
        LIMIT 1
        """
        rows = list(client.execute_query(query, instance_id="operations-db", parameters={"prefix": clean_prefix}))
        if rows:
            r = rows[0]
            audit_status = r["audit_status"] or "UNKNOWN"
            last_ts = r["last_event_ts"] or "N/A"
            txn_cnt = r["txn_count"] or 0
            override_cnt = r["manual_override_count"] or 0
            promo_cnt = r["promo_count"] or 0
            promo_rate = r["promo_rate"] or 0.0
            avg_disc = r["avg_discount_pct"] or 0.0
            tot_disc = r["total_discount_usd"] or 0.0
            live_override_rate = r["live_override_rate"] or 0.0
            risk = r["risk_score"] or 0.0
            row_key = r["row_key"] or "N/A"

            return (
                f"### Cloud Bigtable Live Cashier Metrics (MCP Real-Time Stream)\n"
                f"**Row Key:** `{row_key}`\n"
                f"**Audit Status Flag:** `{audit_status.upper()}`\n"
                f"**Last Event Timestamp:** `{last_ts}`\n"
                f"**Risk Score:** `{risk:.4f}`\n\n"
                f"| Metric | Live 1-Hour Rolling Value |\n"
                f"| --- | --- |\n"
                f"| **1-Hour Transaction Count** | {txn_cnt} |\n"
                f"| **1-Hour Manual Override Count** | {override_cnt} |\n"
                f"| **Calculated Live Override Rate** | **{live_override_rate * 100:.2f}%** ({live_override_rate:.4f}) |\n"
                f"| **1-Hour Promo Applied Count** | {promo_cnt} |\n"
                f"| **1-Hour Promo Rate** | {promo_rate * 100:.2f}% |\n"
                f"| **1-Hour Average Discount Pct** | {avg_disc:.2f}% |\n"
                f"| **1-Hour Total Discount USD** | ${tot_disc:,.2f} |\n"
            )
        else:
            return f"No real-time alert records found in Bigtable instance 'operations-db' matching prefix '{clean_prefix}'."

    except Exception as e:
        logger.error("Error querying Bigtable cashier alerts: %s", e)
        return f"Error retrieving real-time cashier metrics from Bigtable: {e}"
