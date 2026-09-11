"""Portability contract: no environment-specific value may be hardcoded."""

from __future__ import annotations

import pytest

from app import config


def test_project_id_is_resolved_from_environment(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "alpha-project")
    config.get_project_id.cache_clear()
    assert config.get_project_id() == "alpha-project"


def test_project_id_falls_back_to_google_cloud_project(monkeypatch):
    monkeypatch.delenv("PROJECT_ID", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "beta-project")
    config.get_project_id.cache_clear()
    assert config.get_project_id() == "beta-project"


def test_missing_mcp_url_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("BIGTABLE_MCP_URL", raising=False)
    monkeypatch.delenv("MCP_SERVICE_URL", raising=False)
    with pytest.raises(config.ConfigurationError):
        config.get_bigtable_mcp_url()


def test_mcp_url_trailing_slash_is_normalized(monkeypatch):
    monkeypatch.setenv("BIGTABLE_MCP_URL", "https://svc.example.invalid/")
    assert config.get_bigtable_mcp_url() == "https://svc.example.invalid"


def test_model_defaults_to_blueprint_version():
    assert config.get_model_name() == "gemini-3.6-flash"


def test_model_is_overridable(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gemini-3.6-pro")
    assert config.get_model_name() == "gemini-3.6-pro"


def test_similarity_threshold_defaults_to_certified_gate():
    assert config.get_similarity_threshold() == pytest.approx(0.70)


def test_invalid_threshold_degrades_to_safe_default(monkeypatch):
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "not-a-number")
    assert config.get_similarity_threshold() == pytest.approx(0.70)


def test_cost_guardrail_defaults_to_one_gib():
    assert config.get_max_bytes_billed() == 1024 * 1024 * 1024
