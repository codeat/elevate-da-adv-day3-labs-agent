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


def test_agent_binds_all_three_gateways():
    module = _load_agent()
    assert len(module.cymbal_operations_agent.tools) == 3


def test_root_agent_alias_is_exported():
    module = _load_agent()
    assert module.root_agent is module.cymbal_operations_agent


def test_system_instruction_enforces_verbatim_decline_relay():
    module = _load_agent()
    assert "VERBATIM" in module.SYSTEM_INSTRUCTION
    assert "MANDATORY CONCURRENT DISPATCH" in module.SYSTEM_INSTRUCTION
