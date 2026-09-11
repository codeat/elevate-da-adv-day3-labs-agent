"""Data & error contract compliance (Blueprint Sections 2.3 and 4.3)."""

from __future__ import annotations

import re

from app import contracts

_LEAK_PATTERNS = [
    re.compile(r"\{.*error.*\}", re.IGNORECASE),  # f-string interpolation slot
    re.compile(r"Diagnostics", re.IGNORECASE),
    re.compile(r"Traceback", re.IGNORECASE),
    re.compile(r"HTTP \d{3}"),
    re.compile(r"https?://"),  # internal endpoints
]

ALL_CONTRACTS = [
    contracts.OUT_OF_SCOPE_RESPONSE,
    contracts.RAG_SERVICE_UNAVAILABLE_RESPONSE,
    contracts.ANALYTICS_SERVICE_UNAVAILABLE_RESPONSE,
    contracts.ANALYTICS_EMPTY_RESULT_RESPONSE,
    contracts.BIGTABLE_SERVICE_UNAVAILABLE_RESPONSE,
]


def test_out_of_scope_contract_is_exact_and_stable():
    assert contracts.OUT_OF_SCOPE_RESPONSE == (
        "I could not find relevant information in the certified Cymbal Retail POS "
        "hardware knowledge base to answer this question. This request appears to "
        "fall outside the supported scope of store point-of-sale terminal "
        "operations, maintenance, and troubleshooting."
    )


def test_contracts_never_leak_internal_diagnostics():
    for message in ALL_CONTRACTS:
        for pattern in _LEAK_PATTERNS:
            assert not pattern.search(message), (
                f"Contract string leaks internal detail via {pattern.pattern!r}: {message!r}"
            )


def test_contracts_are_non_empty_operator_readable_sentences():
    for message in ALL_CONTRACTS:
        assert len(message) > 40
        assert message.strip().endswith(".")
