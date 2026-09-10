"""POS Hardware Troubleshooting RAG Tool."""

import logging
import os
import re
import time
from google.cloud import bigquery

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "panliuyang-ramp-up-project-01")
DATASET_ID = "cymbal_gold"
TABLE_ID = "pos_manual_chunk_embeddings"
FULL_TABLE = f"`{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
SIMILARITY_THRESHOLD = 0.70


def pos_troubleshooting_rag_tool(query: str) -> str:
    """Retrieve certified hardware troubleshooting runbooks, error recovery SOPs, and repair manuals for POS terminals.

    Use this tool when encountering:
    - Hardware errors and error codes (e.g. ERR-PAY-4001, ERR-DN-PRNT-24V, ERR-DISP-0012).
    - EMV contactless reader freezes, tokenization timeouts, or card reader failures.
    - POS thermal printer paper jams, cutter locks, or power supply issues.
    - Hardware models: Toshiba TCx 810, Clover Station Solo, HP Engage One Pro, Verifone Carbon, Square Register.

    Args:
        query: Specific hardware troubleshooting inquiry or error code (e.g. 'ERR-PAY-4001 EMV contactless payment freeze').

    Returns:
        Certified procedural recovery runbook with stitched surrounding context and clickable GCS document link,
        or a certified safety warning if out-of-scope.
    """
    client = bigquery.Client(project=PROJECT_ID)
    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            # 1. Vector Search with adjacent context stitching
            vector_sql = f"""
            WITH matched AS (
              SELECT
                base.document_filename,
                base.document_title,
                base.equipment_covered,
                base.source_pdf_uri,
                base.chunk_index,
                base.chunk_content,
                ROUND(1 - distance, 4) AS similarity_score
              FROM VECTOR_SEARCH(
                TABLE {FULL_TABLE},
                'embedding',
                (SELECT AI.EMBED(@user_query, endpoint => 'text-embedding-005').result AS embedding),
                top_k => 5,
                distance_type => 'COSINE'
              )
            )
            SELECT
              m.document_filename,
              m.document_title,
              m.equipment_covered,
              REPLACE(m.source_pdf_uri, 'gs://', 'https://storage.cloud.google.com/') AS document_url,
              m.similarity_score,
              m.chunk_index,
              STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_content
            FROM matched m
            JOIN {FULL_TABLE} c
              ON m.document_filename = c.document_filename
             AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
            GROUP BY m.document_filename, m.document_title, m.equipment_covered, document_url, m.similarity_score, m.chunk_index
            ORDER BY m.similarity_score DESC
            LIMIT 1
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("user_query", "STRING", query)
                ],
                maximum_bytes_billed=1024 * 1024 * 1024,  # 1 GB Query Cost & Resource Guardrail
            )
            results = list(client.query(vector_sql, job_config=job_config).result())

            top_score = 0.0
            best_row = None
            if results:
                best_row = results[0]
                top_score = float(best_row.similarity_score)

            # 2. Check if similarity meets threshold
            if top_score >= SIMILARITY_THRESHOLD and best_row:
                return (
                    f"### Certified POS Hardware Runbook\n"
                    f"**Document:** [{best_row.document_title}]({best_row.document_url})\n"
                    f"**Equipment Covered:** {best_row.equipment_covered}\n"
                    f"**Relevance Score:** {top_score:.4f} (Certified >= {SIMILARITY_THRESHOLD})\n\n"
                    f"#### Procedural Recovery Runbook (Stitched Context)\n"
                    f"{best_row.stitched_content}\n\n"
                    f"🔗 **Certified PDF Source:** [{best_row.document_filename}.pdf]({best_row.document_url})"
                )

            # 3. Fallback: Full-Text SEARCH for specific error codes or keywords
            error_codes = re.findall(r'[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+(?:-[A-Z0-9]+)?', query)
            search_token = error_codes[0] if error_codes else query

            search_sql = f"""
            WITH text_matched AS (
              SELECT
                document_filename,
                document_title,
                equipment_covered,
                source_pdf_uri,
                chunk_index,
                chunk_content
              FROM {FULL_TABLE}
              WHERE SEARCH(chunk_content, @search_token)
                 OR chunk_content LIKE CONCAT('%', @raw_token, '%')
              LIMIT 1
            )
            SELECT
              m.document_filename,
              m.document_title,
              m.equipment_covered,
              REPLACE(m.source_pdf_uri, 'gs://', 'https://storage.cloud.google.com/') AS document_url,
              STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_content
            FROM text_matched m
            JOIN {FULL_TABLE} c
              ON m.document_filename = c.document_filename
             AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
            GROUP BY m.document_filename, m.document_title, m.equipment_covered, document_url, m.chunk_index
            LIMIT 1
            """
            search_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("search_token", "STRING", f"`{search_token}`"),
                    bigquery.ScalarQueryParameter("raw_token", "STRING", search_token),
                ],
                maximum_bytes_billed=1024 * 1024 * 1024,  # 1 GB Query Cost & Resource Guardrail
            )
            search_results = list(client.query(search_sql, job_config=search_config).result())

            if search_results:
                s_row = search_results[0]
                return (
                    f"### Certified POS Hardware Runbook (Full-Text Retrieval Fallback)\n"
                    f"**Document:** [{s_row.document_title}]({s_row.document_url})\n"
                    f"**Equipment Covered:** {s_row.equipment_covered}\n"
                    f"**Search Key:** `{search_token}` (Vector similarity: {top_score:.4f} < {SIMILARITY_THRESHOLD})\n\n"
                    f"#### Procedural Recovery Runbook (Stitched Context)\n"
                    f"{s_row.stitched_content}\n\n"
                    f"🔗 **Certified PDF Source:** [{s_row.document_filename}.pdf]({s_row.document_url})"
                )

            # 4. Out-of-scope warning fallback
            return (
                f"Warning: The inquiry regarding '{query}' falls outside the certified Cymbal POS hardware technical scope "
                f"(similarity score {top_score:.4f} < {SIMILARITY_THRESHOLD}). "
                f"No certified operational SOP or hardware runbook is available in the knowledge base."
            )

        except Exception as e:
            last_error = e
            time.sleep(2 ** attempt)

    return (
        f"Warning: Unable to access hardware runbook repository after {max_retries} attempts. "
        f"Diagnostics: {last_error}"
    )
