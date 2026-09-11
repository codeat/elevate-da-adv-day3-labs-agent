"""Canonical API / data / error response contracts.

Every user-facing terminal string emitted by a tool is declared here EXACTLY
ONCE so that the runtime behaviour is provably aligned with the design
blueprint and can be asserted by unit tests.

Blueprint references:
    * Section 2.3  - Out-of-Scope Decline Contract
    * Section 4.3  - Sanitized Fallback Contract (no internal diagnostics leak)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Blueprint Section 2.3 - Out-of-Scope Decline Contract
# ---------------------------------------------------------------------------
# The RAG tool MUST return this string VERBATIM (no interpolation, no score
# echo, no query echo) whenever the retrieval confidence falls below the
# certified similarity threshold AND the full-text fallback yields no match.
OUT_OF_SCOPE_RESPONSE: str = (
    "I cannot find certified warranty or repair rules for this specific error "
    "in our technical repository."
)

# ---------------------------------------------------------------------------
# Blueprint Section 4.3 - Sanitized Fallback Contract
# ---------------------------------------------------------------------------
# On exhausted retries / persistent backend unavailability the tools MUST
# return a shielded notice. Raw exceptions, stack traces, HTTP bodies, host
# names, and internal resource identifiers are logged server-side ONLY and are
# never surfaced to the conversation transcript.
RAG_SERVICE_UNAVAILABLE_RESPONSE: str = (
    "The certified hardware runbook repository is temporarily unavailable. "
    "Please retry shortly, or escalate to the Store Systems Support desk if "
    "the issue persists."
)

ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE: str = (
    "The enterprise retail analytics service is temporarily unavailable. "
    "Please retry shortly, or escalate to the Data Platform Support desk if "
    "the issue persists."
)

ANALYTICS_EMPTY_RESULT_RESPONSE: str = (
    "The analytics request completed successfully but returned no matching "
    "records for the requested scope. Please refine the store, cashier, or "
    "date range filters and try again."
)

BIGTABLE_SERVICE_UNAVAILABLE_RESPONSE: str = (
    "The real-time cashier telemetry stream is temporarily unavailable. "
    "Reconciled historical baselines remain available through the enterprise "
    "analytics tool."
)

__all__ = [
    "OUT_OF_SCOPE_RESPONSE",
    "RAG_SERVICE_UNAVAILABLE_RESPONSE",
    "ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE",
    "ANALYTICS_EMPTY_RESULT_RESPONSE",
    "BIGTABLE_SERVICE_UNAVAILABLE_RESPONSE",
]
