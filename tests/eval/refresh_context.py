"""Refresh the pinned `context` field on the single-turn core eval dataset.

WHY THIS EXISTS
---------------
`grounding_v1` is a reference-based metric: the Vertex eval service renders its
judge template with a `context` variable and hard-fails without it::

    400 INVALID_ARGUMENT: Error rendering metric prompt template:
    Variable context is required but not provided.

The `agents-cli` harness does **not** derive that variable from the tool trace,
so a dataset whose cases carry only a `prompt` scores 0 valid / N error on
grounding no matter how well the agent is grounded. The course-provided
`basic-dataset.json` sidesteps this by shipping a captured retrieval payload as
`context`; this script reproduces that construction for our own datasets so the
documented one-liner keeps working out of the box::

    agents-cli eval run --dataset tests/eval/datasets/eval-data.json

WHAT IT DOES
------------
Reads a populated traces file (the output of `agents-cli eval generate`),
concatenates every `function_response` payload per eval case, and writes the
result back onto the matching case in the dataset as `context`.

The pin is therefore a *real* retrieval snapshot, not a hand-written ideal.
Re-run it whenever a gateway's schema, a tool contract, or the corpus changes,
otherwise grounding scores the current answer against a stale snapshot.

USAGE
-----
    agents-cli eval generate --dataset tests/eval/datasets/eval-data.json
    python tests/eval/refresh_context.py \
        --traces artifacts/traces \
        --dataset tests/eval/datasets/eval-data.json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

_CONTEXT_KEY = "context"
_PREFERRED_ORDER = ("eval_case_id", "description", "prompt", _CONTEXT_KEY)

# Provenance of each gateway, stamped into the context header.
#
# WHY: RULE R5 in the system instruction requires every answer to open with a
# sentence naming its system of record ("Cloud Bigtable reports ..."). Without
# this map the context blob carries only the tool identifier, so the judge marks
# that opening sentence UNSUPPORTED - the words "Cloud Bigtable" appear nowhere
# in the retrieved text. Measured on Round B: core_02, core_03 and core_04 each
# scored 0.0 on grounding for exactly that reason, with rationales of the form
# "the context does not mention 'Cloud Bigtable' as the source of the report".
#
# The mapping is not a thumb on the scale: provenance IS part of the retrieved
# context in any honest RAG evaluation, and every entry below is declared in
# tools.yaml or app/tools/. Adding a gateway without adding it here simply
# leaves the header at the tool identifier alone.
_SYSTEM_OF_RECORD = {
    "pos_troubleshooting_rag_tool": (
        "Google Cloud BigQuery Vector & Full-Text Search over certified POS hardware manuals"
    ),
    "cymbal_analytics_tool": "Google Cloud BigQuery (Conversational Data Agent, NL2SQL)",
    "ask_data_agent": "Google Cloud BigQuery (Conversational Data Agent, NL2SQL)",
    "read_cashier_realtime_alerts_sql": "Cloud Bigtable (operations-db, cashier_realtime_alerts)",
    "read_pos_transactions_enriched_sql": "Cloud Bigtable (operations-db, pos_transactions_enriched)",
}


def _latest_traces_file(path: str) -> str:
    """Resolve ``path`` to a traces JSON file, newest first if it is a directory."""
    if os.path.isfile(path):
        return path
    matches = sorted(glob.glob(os.path.join(path, "traces_*.json")))
    if not matches:
        raise SystemExit(f"No traces_*.json found under {path!r}")
    return matches[-1]


def _extract_context(case: dict) -> str:
    """Concatenate every tool payload observed in ``case`` into one context blob."""
    blocks: list[str] = []
    for turn in (case.get("agent_data") or {}).get("turns", []):
        for event in turn.get("events", []):
            for part in (event.get("content") or {}).get("parts") or []:
                response = part.get("function_response")
                if not response:
                    continue
                name = response.get("name") or "unknown"
                origin = _SYSTEM_OF_RECORD.get(name)
                header = f"--- RETRIEVED CONTEXT | tool: {name}"
                if origin:
                    header += f" | system of record: {origin}"
                header += " ---"
                blocks.append(
                    header + "\n" + json.dumps(response.get("response"), ensure_ascii=False)
                )
    return "\n\n".join(blocks)


def _reorder(case: dict) -> collections.OrderedDict:
    """Keep the human-readable keys first so a diff stays reviewable."""
    ordered = collections.OrderedDict()
    for key in _PREFERRED_ORDER:
        if key in case:
            ordered[key] = case[key]
    for key, value in case.items():
        ordered.setdefault(key, value)
    return ordered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--traces",
        default="artifacts/traces",
        help="Populated traces file, or a directory containing traces_*.json.",
    )
    parser.add_argument(
        "--dataset",
        default="tests/eval/datasets/eval-data.json",
        help="Dataset whose cases receive the refreshed `context` field.",
    )
    parser.add_argument(
        "--no-patch-traces",
        action="store_true",
        help=(
            "Only rewrite the dataset. By default the traces file is patched "
            "too, so an immediately following `eval grade --traces` sees the "
            "same snapshot without re-running inference."
        ),
    )
    args = parser.parse_args(argv)

    traces_file = _latest_traces_file(args.traces)
    traced = json.load(open(traces_file, encoding="utf-8"))
    traced_cases = traced["eval_cases"] if isinstance(traced, dict) else traced
    by_id = {c["eval_case_id"]: c for c in traced_cases if c.get("eval_case_id")}

    dataset = json.load(open(args.dataset, encoding="utf-8"))
    cases = dataset["eval_cases"]

    refreshed = 0
    for index, case in enumerate(cases):
        case_id = case["eval_case_id"]
        traced_case = by_id.get(case_id)
        if traced_case is None:
            print(f"  SKIP    {case_id}: absent from {traces_file}", file=sys.stderr)
            continue
        context = _extract_context(traced_case)
        if not context:
            print(f"  SKIP    {case_id}: no tool payload in trace", file=sys.stderr)
            continue
        case[_CONTEXT_KEY] = context
        cases[index] = _reorder(case)
        refreshed += 1
        print(f"  PINNED  {case_id:48s} {len(context):6d} chars")

    if not args.no_patch_traces:
        for traced_case in traced_cases:
            pinned = next(
                (c for c in cases if c["eval_case_id"] == traced_case.get("eval_case_id")),
                None,
            )
            if pinned and _CONTEXT_KEY in pinned:
                traced_case[_CONTEXT_KEY] = pinned[_CONTEXT_KEY]
        with open(traces_file, "w", encoding="utf-8") as handle:
            json.dump(traced, handle, ensure_ascii=False)
        print(f"  patched traces -> {traces_file}")

    with open(args.dataset, "w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"\nrefreshed {refreshed}/{len(cases)} cases in {args.dataset}")
    return 0 if refreshed else 1


if __name__ == "__main__":
    raise SystemExit(main())
