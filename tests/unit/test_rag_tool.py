"""RAG retrieval logic: error-code boosting and safety contract enforcement."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.contracts import OUT_OF_SCOPE_RESPONSE, RAG_SERVICE_UNAVAILABLE_RESPONSE
from app.tools import rag_tool


# ------------------------------- error-code extraction ---------------------
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Recover from ERR-PAY-4001 EMV freeze", "ERR-PAY-4001"),
        ("thermal cutter lock err-dn-prnt-24v on the TCx", "ERR-DN-PRNT-24V"),
        ("How do I replace the engine oil on a Ford F-150 truck?", None),
        ("The screen is blank", None),
    ],
)
def test_error_code_extraction(query, expected):
    codes = rag_tool._extract_error_codes(query)
    assert (codes[0] if codes else None) == expected


# ------------------------------- SQL keyword boosting ----------------------
def test_vector_sql_injects_keyword_boost_when_error_code_present():
    sql = rag_tool._build_vector_sql(
        "`p.d.t`", has_error_code=True, embedding_endpoint="text-embedding-005"
    )
    assert "@error_code" in sql
    assert "@error_prefix" in sql
    assert "keyword_boost" in sql
    assert "boosted_score" in sql
    assert "ORDER BY m.boosted_score DESC" in sql


def test_vector_sql_omits_boost_for_plain_language_queries():
    sql = rag_tool._build_vector_sql(
        "`p.d.t`", has_error_code=False, embedding_endpoint="text-embedding-005"
    )
    assert "@error_code" not in sql
    assert "0.0 AS keyword_boost" in sql


def test_vector_sql_retains_adjacent_chunk_stitching():
    sql = rag_tool._build_vector_sql(
        "`p.d.t`", has_error_code=True, embedding_endpoint="text-embedding-005"
    )
    assert "BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)" in sql
    assert "STRING_AGG(c.chunk_content" in sql


def test_fulltext_sql_uses_search_predicate():
    sql = rag_tool._build_fulltext_sql("`p.d.t`")
    assert "SEARCH(chunk_content, @search_token)" in sql


# ------------------------------- behavioural contracts ---------------------
def _row(score: float, boost: float = 0.0):
    row = MagicMock()
    row.similarity_score = score
    row.keyword_boost = boost
    row.boosted_score = min(1.0, score + boost)
    row.document_title = "Toshiba TCx 810 Service Manual"
    row.document_url = "https://storage.cloud.google.com/bucket/tcx810.pdf"
    row.document_filename = "tcx810"
    row.equipment_covered = "Toshiba TCx 810"
    row.stitched_content = "Step 1. Power cycle the EMV reader."
    return row


@patch("app.tools.rag_tool.bigquery.Client")
def test_below_threshold_returns_verbatim_out_of_scope_contract(mock_client):
    instance = mock_client.return_value
    instance.query.return_value.result.side_effect = [
        iter([_row(0.31)]),  # vector search - below the 0.70 gate
        iter([]),  # full-text fallback - no match
    ]
    assert rag_tool.pos_troubleshooting_rag_tool("How do I change the oil on a Ford F-150?") == (
        OUT_OF_SCOPE_RESPONSE
    )


@patch("app.tools.rag_tool.bigquery.Client")
def test_boost_lifts_borderline_error_code_match_above_gate(mock_client):
    instance = mock_client.return_value
    # 0.62 base is below the gate; +0.15 exact-code boost certifies it.
    instance.query.return_value.result.return_value = iter([_row(0.62, boost=0.15)])
    result = rag_tool.pos_troubleshooting_rag_tool("ERR-PAY-4001 EMV contactless freeze")
    assert "Certified POS Hardware Runbook" in result
    assert "error-code boost" in result


@patch("app.tools.rag_tool.time.sleep", return_value=None)
@patch("app.tools.rag_tool.bigquery.Client")
def test_persistent_failure_returns_sanitized_notice(mock_client, _sleep):
    mock_client.return_value.query.side_effect = RuntimeError(
        "bigtable-internal-host.prod.google.com refused connection: token=SECRET"
    )
    result = rag_tool.pos_troubleshooting_rag_tool("ERR-PAY-4001")
    assert result == RAG_SERVICE_UNAVAILABLE_RESPONSE
    assert "SECRET" not in result
    assert "Diagnostics" not in result
    assert "google.com" not in result


def test_vector_sql_inlines_embedding_endpoint_as_literal():
    """AI.EMBED rejects parameterized endpoints (400 must be a string literal)."""
    sql = rag_tool._build_vector_sql(
        "`p.d.t`", has_error_code=False, embedding_endpoint="text-embedding-005"
    )
    assert "endpoint => 'text-embedding-005'" in sql
    assert "@embedding_endpoint" not in sql


def test_malicious_embedding_endpoint_is_rejected_before_interpolation():
    import pytest

    from app import config

    for hostile in ["a' OR '1'='1", "text-embedding-005'); DROP TABLE x--", ""]:
        with pytest.raises(config.ConfigurationError):
            rag_tool._build_vector_sql("`p.d.t`", has_error_code=False, embedding_endpoint=hostile)
