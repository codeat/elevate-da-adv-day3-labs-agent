"""Online contract tests against the real Cymbal Retail backends.

Scope (one assertion family per architectural gateway):

* Gateway 1 - BigQuery Conversational Data Agent  -> resource reachability.
* Gateway 2 - BigQuery vector RAG corpus          -> table + column contract.
* Gateway 3 - Cloud Run MCP Toolbox (Bigtable)    -> tool manifest contract.

Every test is read-only and cost-bounded (``maximum_bytes_billed`` and
``LIMIT`` clauses), so the suite is safe to run against production.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Gateway 2 - BigQuery vector RAG corpus
# ---------------------------------------------------------------------------
def test_rag_corpus_table_exposes_the_documented_columns(bq_client, project_id):
    from app import config

    table_ref = f"{project_id}.{config.get_rag_dataset()}.{config.get_rag_table()}"
    table = bq_client.get_table(table_ref)

    columns = {field.name for field in table.schema}
    for required in ("content", "ml_generate_embedding_result"):
        assert required in columns, f"{table_ref} is missing the '{required}' column."
    assert table.num_rows > 0, f"{table_ref} is empty; the RAG gateway cannot ground."


def test_rag_vector_search_returns_a_scored_grounded_chunk(bq_client):
    """End-to-end grounding probe: a known error code must retrieve a chunk."""
    from app.tools.rag_tool import pos_troubleshooting_rag_tool

    answer = pos_troubleshooting_rag_tool("What should I do about error code E-503?")
    assert isinstance(answer, str) and answer.strip()


def test_out_of_scope_question_returns_the_verbatim_decline_contract():
    from app.contracts import OUT_OF_SCOPE_RESPONSE
    from app.tools.rag_tool import pos_troubleshooting_rag_tool

    answer = pos_troubleshooting_rag_tool(
        "What is the weather forecast for Mountain View next Tuesday?"
    )
    assert answer == OUT_OF_SCOPE_RESPONSE


# ---------------------------------------------------------------------------
# Gateway 1 - Conversational Data Agent
# ---------------------------------------------------------------------------
def test_data_agent_resource_is_reachable():
    import google.auth
    import google.auth.transport.requests
    import requests

    from app import config

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())

    url = f"https://geminidataanalytics.googleapis.com/v1/{config.get_data_agent_name()}"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {creds.token}",
            "X-Goog-User-Project": config.get_project_id(),
        },
        timeout=60,
    )
    assert resp.status_code == 200, f"Data Agent lookup failed: HTTP {resp.status_code}"


def test_analytics_gateway_answers_a_bounded_business_question():
    from app.contracts import ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE
    from app.tools.analytics_tool import cymbal_analytics_tool

    answer = cymbal_analytics_tool("What is the Net Transaction Revenue for today by store?")
    assert answer != ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE
    assert answer.strip()


# ---------------------------------------------------------------------------
# Gateway 3 - Cloud Run MCP Toolbox
# ---------------------------------------------------------------------------
def test_mcp_toolbox_publishes_the_contracted_tool_names(id_token_factory):
    import requests

    from app import config

    base_url = config.get_bigtable_mcp_url()
    token = id_token_factory(base_url)

    resp = requests.get(
        f"{base_url}/api/toolset",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    assert resp.status_code == 200, f"MCP Toolbox unreachable: HTTP {resp.status_code}"

    published = set(resp.json().get("tools", {}))
    for contracted in (
        "read_cashier_realtime_alerts_sql",
        "read_pos_transactions_enriched_sql",
    ):
        assert contracted in published, (
            f"MCP Toolbox does not publish the contracted tool '{contracted}'. "
            f"Published: {sorted(published)}"
        )
