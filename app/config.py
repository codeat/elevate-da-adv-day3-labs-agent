"""Centralized, environment-driven runtime configuration.

DESIGN CONTRACT (Blueprint Section 4.1 - Portability & Clean-Run):
    NO environment-specific value (GCP Project ID, region, Cloud Run endpoint,
    Data Agent ID) may ever be hardcoded inside tool modules or YAML manifests.
    Every value is resolved exclusively from process environment variables so
    the identical artifact can be promoted across dev / staging / prod without
    a single code edit.

Resolution order for the project id:
    1. ``PROJECT_ID``
    2. ``GOOGLE_CLOUD_PROJECT``   (ADK / gcloud standard)
    3. ``GCP_PROJECT``            (legacy runtime standard)
    4. Application Default Credentials quota project (discovered at runtime)

If none resolve, a :class:`ConfigurationError` is raised at first use rather
than silently defaulting to a foreign tenant's project.
"""

from __future__ import annotations

import functools
import logging
import os

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """Raised when a mandatory environment property is not configured."""


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


@functools.lru_cache(maxsize=1)
def get_project_id() -> str:
    """Resolve the active GCP project id from the environment (never hardcoded)."""
    project_id = _first_env("PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT")
    if project_id:
        return project_id

    # Last resort: infer from Application Default Credentials.
    try:
        import google.auth

        _, adc_project = google.auth.default()
        if adc_project:
            logger.warning(
                "PROJECT_ID is unset; falling back to the ADC quota project. "
                "Set PROJECT_ID explicitly for deterministic deployments."
            )
            return adc_project
    except Exception:  # pragma: no cover - defensive, ADC may be absent
        logger.debug("Unable to infer project from Application Default Credentials.")

    raise ConfigurationError(
        "No GCP project configured. Export PROJECT_ID (or GOOGLE_CLOUD_PROJECT) "
        "before starting the agent. See .env.example."
    )


def get_location() -> str:
    """Regional location tag for the Conversational Data Agent API."""
    return _first_env("LOCATION", "GOOGLE_CLOUD_LOCATION") or "global"


def get_region() -> str:
    """Compute/serving region for Cloud Run and Agent Runtime deployments."""
    return _first_env("REGION") or "us-central1"


def get_model_name() -> str:
    """Coordinator LLM identifier (overridable per environment)."""
    return _first_env("MODEL_NAME", "COORDINATOR_MODEL") or "gemini-3.6-flash"


def get_data_agent_id() -> str:
    """BigQuery Conversational Data Agent identifier."""
    return _first_env("DATA_AGENT_ID") or "cymbal-retail-analytics-agent"


def get_data_agent_name() -> str:
    """Fully-qualified Data Agent resource name."""
    return (
        f"projects/{get_project_id()}/locations/{get_location()}/dataAgents/{get_data_agent_id()}"
    )


def get_chat_url() -> str:
    """Gemini Data Analytics conversational chat endpoint."""
    return (
        "https://geminidataanalytics.googleapis.com/v1/projects/"
        f"{get_project_id()}/locations/{get_location()}:chat"
    )


def get_bigtable_mcp_url() -> str:
    """Cloud Run MCP Toolbox base URL. MUST be supplied by the environment."""
    url = _first_env("BIGTABLE_MCP_URL", "MCP_SERVICE_URL")
    if not url:
        raise ConfigurationError(
            "BIGTABLE_MCP_URL is not configured. Export the Cloud Run MCP Toolbox "
            "endpoint (e.g. https://mcp-toolbox-bigtable-<hash>-<region>.run.app). "
            "See .env.example."
        )
    return url.rstrip("/")


def get_bigtable_instance() -> str:
    """Cloud Bigtable instance backing the real-time cashier alert stream."""
    return _first_env("BIGTABLE_INSTANCE") or "operations-db"


def get_rag_dataset() -> str:
    """BigQuery dataset holding the chunked POS manual embeddings."""
    return _first_env("RAG_DATASET_ID") or "cymbal_gold"


def get_rag_table() -> str:
    """BigQuery table holding the chunked POS manual embeddings."""
    return _first_env("RAG_TABLE_ID") or "pos_manual_chunk_embeddings"


def get_rag_embedding_endpoint() -> str:
    """Vertex AI text embedding endpoint used by ``AI.EMBED``."""
    return _first_env("RAG_EMBEDDING_ENDPOINT") or "text-embedding-005"


def get_similarity_threshold() -> float:
    """Minimum cosine similarity required to certify a runbook answer."""
    raw = _first_env("RAG_SIMILARITY_THRESHOLD")
    try:
        return float(raw) if raw else 0.70
    except ValueError:
        logger.warning("Invalid RAG_SIMILARITY_THRESHOLD=%r; using 0.70.", raw)
        return 0.70


def get_max_bytes_billed() -> int:
    """Hard BigQuery cost guardrail (bytes). Defaults to 1 GiB."""
    raw = _first_env("BQ_MAX_BYTES_BILLED")
    try:
        return int(raw) if raw else 1024 * 1024 * 1024
    except ValueError:
        logger.warning("Invalid BQ_MAX_BYTES_BILLED=%r; using 1 GiB.", raw)
        return 1024 * 1024 * 1024


def get_max_retries() -> int:
    """Transient-fault retry budget for all outbound backend calls."""
    raw = _first_env("TOOL_MAX_RETRIES")
    try:
        return max(1, int(raw)) if raw else 3
    except ValueError:
        return 3


# ---------------------------------------------------------------------------
# Observability (BigQuery Agent Analytics)
# ---------------------------------------------------------------------------
def get_telemetry_dataset() -> str:
    """BigQuery dataset receiving the streamed agent interaction events."""
    return _first_env("BQ_TELEMETRY_DATASET", "TELEMETRY_DATASET_ID") or "agent_telemetry"


def get_telemetry_table() -> str:
    """Event table inside the telemetry dataset.

    The lab contract fixes this at ``events`` (the ADK plugin default is
    ``agent_events``), because the operational dashboard notebook and the
    telemetry Data Agent both bind to ``<dataset>.events``.
    """
    return _first_env("BQ_TELEMETRY_TABLE") or "events"


def get_telemetry_location() -> str:
    """BigQuery location of the telemetry dataset (must match the dataset)."""
    return _first_env("BQ_TELEMETRY_LOCATION") or get_region()


def is_telemetry_enabled() -> bool:
    """Whether interaction events are streamed to BigQuery.

    Enabled by default; set ``BQ_TELEMETRY_ENABLED=0`` to run the agent fully
    offline (unit tests, air-gapped demos) without a telemetry sink.
    """
    raw = _first_env("BQ_TELEMETRY_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}
