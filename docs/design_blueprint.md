# Design Blueprint — Cymbal Retail Operations Coordinator Agent

Normative specification for the `cymbal_operations_agent` implementation.
Section numbers referenced from source code docstrings and unit tests map here.

---

## 1. Architecture

### 1.1 Topology
Hub-and-spoke. A single ADK coordinator owns intent routing; three decoupled
gateways own their respective backends. Gateways never call each other.

| Gateway | Transport | Backend |
| :--- | :--- | :--- |
| `ask_data_agent` (ADK-native `DataAgentToolset`) | ADK first-party Data Agent integration | BigQuery `cymbal_gold`, `cymbal_governance`, AWS S3 BigLake |
| `cymbal_analytics_tool` | REST (`geminidataanalytics.googleapis.com`) — **degraded fallback only** | same as above |
| `pos_troubleshooting_rag_tool` | BigQuery client | `pos_manual_chunk_embeddings` (vector + full-text) |
| Bigtable MCP toolset | MCP over SSE | Cloud Run Toolbox → Bigtable `operations-db` |

The analytics gateway is bound through ADK's **native** `DataAgentToolset`
(`google.adk.tools.data_agent`), filtered to the read-only `ask_data_agent`
tool with `enable_data_agent_modification=False` and `max_query_result_rows=50`.
Hand-rolled HTTP is retained purely as a degraded adapter for runtimes where the
native toolset cannot be constructed; the native path is always preferred.

### 1.2 Model
The coordinator runs `gemini-3.6-flash`, resolved via `MODEL_NAME`. No model
identifier is hardcoded in `agent.py`.

### 1.3 Dispatch modes
1. **Single dispatch** — direct domain lookups.
2. **Parallel dispatch** — live Bigtable telemetry *and* the BigQuery 7-day
   baseline are invoked concurrently within Turn 1 (never serialized).
3. **Sequential multi-turn dispatch** — Turn 1 ranks offenders in GCP; Turn 2
   propagates the extracted cashier id into the federated AWS S3 audit.

---

## 2. Retrieval Specification

### 2.1 Chunking & embeddings
500-character sliding windows with 100-character overlap; dense vectors from
`text-embedding-005` (`RETRIEVAL_DOCUMENT` task type); adjacent chunks
`N-1 .. N+1` stitched with `STRING_AGG` at query time.

### 2.2 Error-code keyword boosting (SQL-side)
Fault codes are diluted inside long prose, so cosine similarity alone
under-ranks the exact runbook chunk. The vector query therefore computes a
boosted score **inside SQL**:

```sql
CASE
  WHEN UPPER(base.chunk_content) LIKE CONCAT('%', @error_code, '%')  THEN 0.15
  WHEN @error_prefix != ''
       AND UPPER(base.chunk_content) LIKE CONCAT('%', @error_prefix, '%') THEN 0.05
  ELSE 0.0
END AS keyword_boost
...
LEAST(1.0, ROUND(1 - distance, 4) + keyword_boost) AS boosted_score
...
ORDER BY m.boosted_score DESC, m.similarity_score DESC
```

* `@error_code` — the full canonical code, e.g. `ERR-PAY-4001`.
* `@error_prefix` — the code family, e.g. `ERR-PAY`.
* `top_k` is widened to 10 so boosting can genuinely re-rank candidates.
* Boosting is omitted entirely for plain-language queries.

### 2.3 Out-of-Scope Decline Contract  *(normative)*
When `boosted_score < RAG_SIMILARITY_THRESHOLD` (default `0.70`) **and** the
full-text `SEARCH()` fallback returns no row, the tool MUST return this string
**verbatim**, with no interpolation, score echo, or query echo:

> I cannot find certified warranty or repair rules for this specific error in our technical repository.

Implemented as `app.contracts.OUT_OF_SCOPE_RESPONSE`; asserted by
`tests/unit/test_contracts.py::test_out_of_scope_contract_is_exact_and_stable`.
The coordinator system instruction requires relaying it verbatim.

---

## 3. Data Contracts

| Field | Type | Notes |
| :--- | :--- | :--- |
| `similarity_score` | FLOAT64 | `ROUND(1 - distance, 4)`, cosine. |
| `keyword_boost` | FLOAT64 | `0.00` \| `0.05` \| `0.15`. |
| `boosted_score` | FLOAT64 | `LEAST(1.0, similarity + boost)` — the gating value. |
| `document_url` | STRING | `gs://` rewritten to `https://storage.cloud.google.com/`. |
| `live_override_rate` | FLOAT64 | `manual_override_count / txn_count`, 4 dp. |

Currency renders as `$X,XXX.XX`; rates render as `XX.XX%`.

### 3.2 MCP Tool Naming Contract  *(normative)*
Every tool published by the Cloud Run MCP Toolbox follows `read_<table>_sql`,
which encodes the access mode (`read`), the backing Bigtable table, and the
query dialect. Renaming a tool is a breaking contract change.

| MCP tool | Backing table | Parameters | Purpose |
| :--- | :--- | :--- | :--- |
| `read_cashier_realtime_alerts_sql` | `cashier_realtime_alerts` | `prefix` | Live 1-hour rolling override rate, audit flag, risk score. |
| `read_pos_transactions_enriched_sql` | `pos_transactions_enriched` | `prefix`, `row_limit` | Individual enriched checkout transactions behind an alert. |

Both declarations live in `tools.yaml` (templated with `${PROJECT_ID}` /
`${BIGTABLE_INSTANCE}`) and are asserted online by
`tests/integration/test_live_backends.py::test_mcp_toolbox_publishes_the_contracted_tool_names`
and offline by `tests/unit/test_agent_wiring.py`.

---

## 4. Non-Functional Requirements

### 4.1 Portability & Clean-Run  *(normative)*
No environment-specific literal — GCP project id, region, Cloud Run endpoint,
Data Agent id, Bigtable instance — may appear in any `.py`, `.yaml`, or `.tf`
file. All values resolve through `app/config.py`. `tools.yaml` ships templated
(`${PROJECT_ID}`) and is rendered at deploy time by `make mcp-config`.
Enforced by `tests/unit/test_no_hardcoded_environment.py` in CI and pre-commit.

### 4.2 Cost & Resource Guardrails
Every BigQuery job carries `maximum_bytes_billed` (`BQ_MAX_BYTES_BILLED`,
default 1 GiB). Outbound calls use bounded exponential backoff
(`TOOL_MAX_RETRIES`, default 3).

### 4.3 Sanitized Fallback Contract  *(normative)*
Raw exceptions, stack traces, HTTP status codes, response bodies, internal
hostnames, resource paths, and tokens MUST NOT reach the conversation
transcript. They are captured with `logger.exception(...)` server-side. Callers
receive the operator-readable notices in `app/contracts.py`. Asserted by
`test_contracts.py` and the leak tests in `test_rag_tool.py` /
`test_analytics_tool.py`.

### 4.4 Security & IAM
Runtime identity `cymbal-sa-data@<project>` holds only `bigquery.jobUser`,
`bigquery.dataViewer`, `bigtable.reader`, `aiplatform.user`, and
`secretmanager.secretAccessor`. The MCP Cloud Run service is deployed
`--no-allow-unauthenticated` and invoked with OIDC identity tokens. The MCP
manifest lives in Secret Manager, never on disk in the image.

---

## 5. Operational Readiness

| Asset | Location |
| :--- | :--- |
| Setup / quality / deploy automation | `Makefile` |
| Container image | `Dockerfile` (multi-stage, non-root uid 1001, healthcheck) |
| Infrastructure as Code | `deploy/terraform/` |
| Continuous integration | `.github/workflows/ci.yaml` |
| Local guardrails | `.pre-commit-config.yaml` |
| Offline unit tests | `tests/unit/` (hermetic, default `pytest` target) |
| Online integration tests | `tests/integration/` (opt-in via `RUN_INTEGRATION_TESTS=1`, `make test-integration`) |
| Golden evaluation suite | `tests/eval/` |

### 5.1 Test Strategy
Two layers, deliberately separated so a developer without ADC still gets a
green build:

1. **Unit layer** — fully mocked, no socket and no credential resolution. Runs
   on every push and in pre-commit.
2. **Integration layer** — real BigQuery / Data Agent / Cloud Run MCP calls,
   read-only and cost-bounded. Skipped unless `RUN_INTEGRATION_TESTS=1`; in CI
   it runs as a keyless (Workload Identity Federation) `workflow_dispatch` job.
