"""Analytics gateway: sanitized failure paths and contract compliance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.contracts import (
    ANALYTICS_EMPTY_RESULT_RESPONSE,
    ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE,
)
from app.tools import analytics_tool


@patch("app.tools.analytics_tool.time.sleep", return_value=None)
@patch("app.tools.analytics_tool.google.auth.default")
def test_auth_failure_returns_sanitized_notice(mock_auth, _sleep):
    mock_auth.side_effect = RuntimeError(
        "Permission denied on projects/acme-secret-tenant/locations/global"
    )
    result = analytics_tool.cymbal_analytics_tool("Net Transaction Revenue by store")
    assert result == ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE
    assert "acme-secret-tenant" not in result
    assert "Diagnostics" not in result


@patch("app.tools.analytics_tool.time.sleep", return_value=None)
@patch("app.tools.analytics_tool.requests.post")
@patch("app.tools.analytics_tool.google.auth.default")
def test_http_error_body_is_never_echoed(mock_auth, mock_post, _sleep):
    mock_auth.return_value = (MagicMock(token="tok"), "unit-test-project")
    response = MagicMock()
    response.status_code = 403
    response.text = "IAM denied for serviceAccount:internal-sa@acme.iam.gserviceaccount.com"
    mock_post.return_value = response

    result = analytics_tool.cymbal_analytics_tool("Estimated Cover Hours")
    assert result == ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE
    assert "iam.gserviceaccount.com" not in result
    assert "403" not in result


@patch("app.tools.analytics_tool.requests.post")
@patch("app.tools.analytics_tool.google.auth.default")
def test_empty_payload_returns_contract_string(mock_auth, mock_post):
    mock_auth.return_value = (MagicMock(token="tok"), "unit-test-project")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = []
    mock_post.return_value = response

    assert analytics_tool.cymbal_analytics_tool("q") == ANALYTICS_EMPTY_RESULT_RESPONSE


@patch("app.tools.analytics_tool.requests.post")
@patch("app.tools.analytics_tool.google.auth.default")
def test_successful_response_renders_sql_and_table(mock_auth, mock_post):
    mock_auth.return_value = (MagicMock(token="tok"), "unit-test-project")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [
        {"systemMessage": {"generatedSql": {"query": "SELECT 1"}}},
        {
            "systemMessage": {
                "data": {
                    "schema": {"fields": [{"name": "store_id"}, {"name": "revenue"}]},
                    "rows": [["STORE_048", 1234.5]],
                }
            }
        },
        {"systemMessage": {"text": {"parts": ["Running a query...", "Store 048 leads."]}}},
    ]
    mock_post.return_value = response

    result = analytics_tool.cymbal_analytics_tool("Net Transaction Revenue")
    assert "```sql" in result
    assert "| store_id | revenue |" in result
    assert "Store 048 leads." in result
    # Engine progress chatter must be filtered out of the transcript.
    assert "Running a query" not in result
