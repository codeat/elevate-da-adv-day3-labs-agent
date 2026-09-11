"""Reference copy of the extraction helpers embedded in each metric module.

WHY THE DUPLICATION IS DELIBERATE
---------------------------------
``agents-cli`` loads a ``custom_function_file`` by reading the file as *source
text* and ``exec``-ing it into a bare namespace (see
``google/agents/cli/eval/eval_utils.py::_compile_custom_function``). There is no
package context, no ``sys.path`` entry, and no module identity - so a metric
file physically cannot ``import`` a sibling helper module. Each metric is
therefore self-contained by construction.

This module is never loaded by the evaluation harness. It exists so the shared
contract has exactly one reviewable definition, and so the unit tests in
``tests/unit/test_eval_metrics.py`` can assert that every metric file stays in
sync with it.
"""

from __future__ import annotations

from typing import Any


def part_texts(content: Any) -> str:
    """Concatenate every ``text`` part of a google.genai ``Content`` dict."""
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts") or []
    return "\n".join(p.get("text") or "" for p in parts if isinstance(p, dict) and p.get("text"))


def final_response_text(instance: dict) -> str:
    """Final model-visible answer for the case under evaluation."""
    return part_texts(instance.get("response"))


def user_prompt_text(instance: dict) -> str:
    """Prompt that elicited the response, including multi-turn continuations."""
    direct = part_texts(instance.get("prompt"))
    if direct:
        return direct
    # Multi-turn cases carry the conversation under agent_data.turns instead.
    turns = (instance.get("agent_data") or {}).get("turns") or []
    for turn in reversed(turns):
        for event in reversed(turn.get("events") or []):
            if event.get("author") == "user":
                return part_texts(event.get("content"))
    return ""


def invoked_tool_names(instance: dict) -> list[str]:
    """Every tool the agent actually called, in invocation order."""
    names: list[str] = []
    turns = (instance.get("agent_data") or {}).get("turns") or []
    for turn in turns:
        for event in turn.get("events") or []:
            for part in (event.get("content") or {}).get("parts") or []:
                call = part.get("function_call") if isinstance(part, dict) else None
                if call and call.get("name"):
                    names.append(call["name"])
    return names
