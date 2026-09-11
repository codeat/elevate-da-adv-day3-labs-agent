"""POS Hardware Troubleshooting RAG Tool.

Implements the certified hardware runbook retrieval path:
    1. ``VECTOR_SEARCH`` over chunked manual embeddings (COSINE distance).
    2. SQL-side **error-code keyword boosting** so that an exact fault code
       match (e.g. ``ERR-PAY-4001``) is promoted above a merely semantically
       similar chunk (Blueprint Section 2.2).
    3. Adjacent chunk stitching (N-1 .. N+1) via ``STRING_AGG``.
    4. Certified similarity gate (default 0.70) with full-text ``SEARCH()``
       fallback.
    5. Verbatim out-of-scope decline contract (Blueprint Section 2.3).
    6. Sanitized, shielded failure notice (Blueprint Section 4.3) - internal
       diagnostics are logged, never returned to the caller.

All environment-specific values are resolved from :mod:`app.config`; nothing
is hardcoded in this module.
"""

from __future__ import annotations

import logging
import re
import time

from google.cloud import bigquery

from app import config
from app.contracts import OUT_OF_SCOPE_RESPONSE, RAG_SERVICE_UNAVAILABLE_RESPONSE

logger = logging.getLogger(__name__)

# Matches canonical Cymbal fault codes: ERR-PAY-4001, ERR-DN-PRNT-24V, ...
ERROR_CODE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{1,6}(?:-[A-Z0-9]{1,6}){1,3}\b")

# SQL-side keyword boost applied on top of the normalized cosine similarity.
EXACT_CODE_BOOST = 0.15
PARTIAL_CODE_BOOST = 0.05


def _extract_error_codes(query: str) -> list[str]:
    """Extract canonical hardware fault codes from a free-text query."""
    return ERROR_CODE_PATTERN.findall(query.upper())


def _build_vector_sql(full_table: str, has_error_code: bool) -> str:
    """Compose the vector search SQL, injecting keyword boosting when applicable."""
    if has_error_code:
        boost_expr = f"""
            CASE
              WHEN UPPER(base.chunk_content) LIKE CONCAT('%', @error_code, '%')
                THEN {EXACT_CODE_BOOST}
              WHEN @error_prefix != ''
                   AND UPPER(base.chunk_content) LIKE CONCAT('%', @error_prefix, '%')
                THEN {PARTIAL_CODE_BOOST}
              ELSE 0.0
            END"""
    else:
        boost_expr = "0.0"

    # S608: `full_table` is a fully-qualified identifier composed exclusively from
    # trusted configuration (never user input); BigQuery identifiers cannot be bound
    # as query parameters. All user-supplied values travel as ScalarQueryParameters.
    return f"""
    WITH matched AS (
      SELECT
        base.document_filename,
        base.document_title,
        base.equipment_covered,
        base.source_pdf_uri,
        base.chunk_index,
        base.chunk_content,
        ROUND(1 - distance, 4) AS similarity_score,
        -- SQL-side error-code keyword boosting (Blueprint Section 2.2)
        {boost_expr} AS keyword_boost,
        LEAST(1.0, ROUND(1 - distance, 4) + ({boost_expr})) AS boosted_score
      FROM VECTOR_SEARCH(
        TABLE {full_table},
        'embedding',
        (SELECT AI.EMBED(@user_query, endpoint => @embedding_endpoint).result AS embedding),
        top_k => 10,
        distance_type => 'COSINE'
      )
    )
    SELECT
      m.document_filename,
      m.document_title,
      m.equipment_covered,
      REPLACE(m.source_pdf_uri, 'gs://', 'https://storage.cloud.google.com/') AS document_url,
      m.similarity_score,
      m.keyword_boost,
      m.boosted_score,
      m.chunk_index,
      STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_content
    FROM matched m
    JOIN {full_table} c
      ON m.document_filename = c.document_filename
     AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
    GROUP BY
      m.document_filename, m.document_title, m.equipment_covered, document_url,
      m.similarity_score, m.keyword_boost, m.boosted_score, m.chunk_index
    ORDER BY m.boosted_score DESC, m.similarity_score DESC
    LIMIT 1
    """


def _build_fulltext_sql(full_table: str) -> str:
    """Compose the deterministic full-text fallback SQL."""
    # S608: identifier-only interpolation - see the note in _build_vector_sql.
    return f"""
    WITH text_matched AS (
      SELECT
        document_filename,
        document_title,
        equipment_covered,
        source_pdf_uri,
        chunk_index,
        chunk_content
      FROM {full_table}
      WHERE SEARCH(chunk_content, @search_token)
         OR UPPER(chunk_content) LIKE CONCAT('%', UPPER(@raw_token), '%')
      LIMIT 1
    )
    SELECT
      m.document_filename,
      m.document_title,
      m.equipment_covered,
      REPLACE(m.source_pdf_uri, 'gs://', 'https://storage.cloud.google.com/') AS document_url,
      STRING_AGG(c.chunk_content, '\\n' ORDER BY c.chunk_index ASC) AS stitched_content
    FROM text_matched m
    JOIN {full_table} c
      ON m.document_filename = c.document_filename
     AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
    GROUP BY m.document_filename, m.document_title, m.equipment_covered, document_url, m.chunk_index
    LIMIT 1
    """


def pos_troubleshooting_rag_tool(query: str) -> str:
    """Retrieve certified hardware troubleshooting runbooks and error recovery SOPs for POS terminals.

    Use this tool when encountering:
    - Hardware errors and error codes (e.g. ERR-PAY-4001, ERR-DN-PRNT-24V, ERR-DISP-0012).
    - EMV contactless reader freezes, tokenization timeouts, or card reader failures.
    - POS thermal printer paper jams, cutter locks, or power supply issues.
    - Hardware models: Toshiba TCx 810, Clover Station Solo, HP Engage One Pro, Verifone Carbon, Square Register.

    Args:
        query: Specific hardware troubleshooting inquiry or error code
            (e.g. 'ERR-PAY-4001 EMV contactless payment freeze').

    Returns:
        A certified procedural recovery runbook with stitched surrounding context
        and a clickable GCS document link. If the retrieval confidence falls below
        the certified similarity threshold and no full-text match exists, the
        canonical out-of-scope decline contract is returned verbatim.
    """
    project_id = config.get_project_id()
    full_table = f"`{project_id}.{config.get_rag_dataset()}.{config.get_rag_table()}`"
    threshold = config.get_similarity_threshold()
    max_bytes = config.get_max_bytes_billed()
    max_retries = config.get_max_retries()

    error_codes = _extract_error_codes(query)
    primary_code = error_codes[0] if error_codes else ""
    # 'ERR-PAY-4001' -> 'ERR-PAY' partial family boost.
    code_parts = primary_code.split("-")
    error_prefix = "-".join(code_parts[:2]) if len(code_parts) >= 3 else ""

    client = bigquery.Client(project=project_id)
    vector_sql = _build_vector_sql(full_table, bool(primary_code))

    for attempt in range(1, max_retries + 1):
        try:
            params = [
                bigquery.ScalarQueryParameter("user_query", "STRING", query),
                bigquery.ScalarQueryParameter(
                    "embedding_endpoint", "STRING", config.get_rag_embedding_endpoint()
                ),
            ]
            if primary_code:
                params.append(bigquery.ScalarQueryParameter("error_code", "STRING", primary_code))
                params.append(bigquery.ScalarQueryParameter("error_prefix", "STRING", error_prefix))

            job_config = bigquery.QueryJobConfig(
                query_parameters=params,
                maximum_bytes_billed=max_bytes,
            )
            results = list(client.query(vector_sql, job_config=job_config).result())

            top_score = 0.0
            best_row = None
            if results:
                best_row = results[0]
                top_score = float(best_row.boosted_score)

            # --- Certified similarity gate -------------------------------
            if best_row is not None and top_score >= threshold:
                boost = float(best_row.keyword_boost or 0.0)
                boost_note = (
                    f" (base {float(best_row.similarity_score):.4f} + error-code boost {boost:.2f})"
                    if boost > 0
                    else ""
                )
                return (
                    "### Certified POS Hardware Runbook\n"
                    f"**Document:** [{best_row.document_title}]({best_row.document_url})\n"
                    f"**Equipment Covered:** {best_row.equipment_covered}\n"
                    f"**Relevance Score:** {top_score:.4f} "
                    f"(Certified >= {threshold}){boost_note}\n\n"
                    "#### Procedural Recovery Runbook (Stitched Context)\n"
                    f"{best_row.stitched_content}\n\n"
                    f"🔗 **Certified PDF Source:** "
                    f"[{best_row.document_filename}.pdf]({best_row.document_url})"
                )

            # --- Full-text deterministic fallback -------------------------
            search_token = primary_code or query
            search_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("search_token", "STRING", f"`{search_token}`"),
                    bigquery.ScalarQueryParameter("raw_token", "STRING", search_token),
                ],
                maximum_bytes_billed=max_bytes,
            )
            search_results = list(
                client.query(_build_fulltext_sql(full_table), job_config=search_config).result()
            )

            if search_results:
                s_row = search_results[0]
                return (
                    "### Certified POS Hardware Runbook (Full-Text Retrieval Fallback)\n"
                    f"**Document:** [{s_row.document_title}]({s_row.document_url})\n"
                    f"**Equipment Covered:** {s_row.equipment_covered}\n"
                    f"**Search Key:** `{search_token}`\n\n"
                    "#### Procedural Recovery Runbook (Stitched Context)\n"
                    f"{s_row.stitched_content}\n\n"
                    f"🔗 **Certified PDF Source:** "
                    f"[{s_row.document_filename}.pdf]({s_row.document_url})"
                )

            # --- Blueprint Section 2.3: verbatim out-of-scope decline ------
            logger.info(
                "RAG out-of-scope decline emitted (boosted_score=%.4f < %.2f).",
                top_score,
                threshold,
            )
            return OUT_OF_SCOPE_RESPONSE

        except Exception:
            # Blueprint Section 4.3: diagnostics stay server-side only.
            logger.exception("RAG retrieval attempt %d/%d failed.", attempt, max_retries)
            if attempt < max_retries:
                time.sleep(2**attempt)

    return RAG_SERVICE_UNAVAILABLE_RESPONSE
