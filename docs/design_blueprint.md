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
| `cymbal_analytics_tool` | REST (`geminidataanalytics.googleapis.com`) | BigQuery `cymbal_gold`, `cymbal_governance`, AWS S3 BigLake |
| `pos_troubleshooting_rag_tool` | BigQuery client | `pos_manual_chunk_embeddings` (vector + full-text) |
| Bigtable MCP toolset | MCP over SSE | Cloud Run Toolbox → Bigtable `operations-db` |

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

> I could not find relevant information in the certified Cymbal Retail POS
> hardware knowledge base to answer this question. This request appears to
> fall outside the supported scope of store point-of-sale terminal
> operations, maintenance, and troubleshooting.

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
| Offline unit tests | `tests/unit/` |
| Golden evaluation suite | `tests/eval/` |
