"""Coordinator topology: hub-and-spoke wiring and model alignment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _load_agent():
    import importlib

    with (
        patch("app.tools.bigtable_tool.McpToolset", MagicMock()),
        patch("app.tools.bigtable_tool._get_id_token", return_value="fake-token"),
    ):
        import app.agent as agent_module

        return importlib.reload(agent_module)


def test_agent_uses_blueprint_mandated_model():
    module = _load_agent()
    assert module.MODEL_NAME == "gemini-3.6-flash"
    assert module.cymbal_operations_agent.model == "gemini-3.6-flash"


def test_agent_binds_every_declared_gateway():
    module = _load_agent()
    # Native Data Agent toolset + REST fallback adapter + RAG + MCP toolbox.
    assert len(module.cymbal_operations_agent.tools) == 4


def test_agent_prefers_native_data_agent_toolset():
    module = _load_agent()
    assert module.data_agent_toolset is module.cymbal_operations_agent.tools[0]


def test_system_instruction_advertises_mcp_contract_tool_names():
    module = _load_agent()
    assert "read_cashier_realtime_alerts_sql" in module.SYSTEM_INSTRUCTION
    assert "read_pos_transactions_enriched_sql" in module.SYSTEM_INSTRUCTION


def test_root_agent_alias_is_exported():
    module = _load_agent()
    assert module.root_agent is module.cymbal_operations_agent


def test_system_instruction_enforces_verbatim_decline_relay():
    module = _load_agent()
    assert "VERBATIM" in module.SYSTEM_INSTRUCTION
    assert "MANDATORY CONCURRENT DISPATCH" in module.SYSTEM_INSTRUCTION
