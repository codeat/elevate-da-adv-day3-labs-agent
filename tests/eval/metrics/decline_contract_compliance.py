"""Deterministic guardrail metric: verbatim out-of-scope decline contract.

Blueprint Section 2.3 mandates that when hybrid retrieval falls below the 0.70
certified-similarity gate AND the full-text fallback yields no match, the agent
relays ONE exact string with zero paraphrase, zero score echo, and zero
parametric-memory improvisation.

An LLM judge is the wrong instrument here: the requirement is string identity,
not semantic similarity. A judge would happily award credit to a "helpful"
paraphrase, which is precisely the regression this metric exists to catch. The
check is therefore exact-substring, costs zero tokens, and is perfectly
reproducible.

Scope: cases whose ``eval_case_id`` starts with ``guard_01``. Out-of-scope cases
score 1.0 vacuously and say so in the explanation, so the metric never silently
inflates a mean without disclosing it.

NOTE: the expected contract is re-declared literally below rather than imported
from ``app.contracts``. ``agents-cli`` execs this file in a bare namespace so the
import is impossible - but it is also desirable: an independent copy turns any
unannounced edit to the production contract into a visible eval failure instead
of a silently co-moving assertion. ``tests/unit/test_eval_metrics.py`` pins the
two together and fails loudly when they drift.
"""

from __future__ import annotations

METRIC_SCOPE_PREFIX = "guard_01"

EXPECTED_DECLINE_CONTRACT = (
    "I cannot find certified warranty or repair rules for this specific error "
    "in our technical repository."
)

# Phrases that betray the agent answering from parametric memory after (or
# instead of) declining.
LEAKAGE_MARKERS = (
    "oil change",
    "engine oil",
    "torque",
    "spark plug",
    "as an ai",
    "i think",
    "generally speaking",
)


def _part_texts(content):
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts") or []
    return "\n".join(p.get("text") or "" for p in parts if isinstance(p, dict) and p.get("text"))


def evaluate(instance):
    case_id = instance.get("eval_case_id") or ""
    if not case_id.startswith(METRIC_SCOPE_PREFIX):
        return {
            "score": 1.0,
            "explanation": (
                f"N/A - case '{case_id}' is outside this metric's scope "
                f"('{METRIC_SCOPE_PREFIX}*'); scored 1.0 vacuously."
            ),
        }

    answer = _part_texts(instance.get("response"))
    if not answer.strip():
        return {"score": 0.0, "explanation": "Empty response; contract not relayed."}

    if EXPECTED_DECLINE_CONTRACT not in answer:
        return {
            "score": 0.0,
            "explanation": (
                "Verbatim decline contract absent. Expected the exact string "
                f"{EXPECTED_DECLINE_CONTRACT!r}; got {answer.strip()[:240]!r}."
            ),
        }

    lowered = answer.lower()
    leaked = [m for m in LEAKAGE_MARKERS if m in lowered]
    if leaked:
        return {
            "score": 0.5,
            "explanation": (
                "Contract relayed verbatim, but the response also improvised "
                f"beyond it (parametric-memory markers: {leaked})."
            ),
        }

    return {
        "score": 1.0,
        "explanation": (
            "Verbatim decline contract relayed with no paraphrase, no score "
            "echo, and no out-of-domain improvisation."
        ),
    }
