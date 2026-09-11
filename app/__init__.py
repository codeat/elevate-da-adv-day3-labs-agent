"""Cymbal Retail Operations Coordinator Agent package.

``app`` (an :class:`~google.adk.apps.App` carrying the BigQuery Agent Analytics
telemetry plugin) and ``root_agent`` are both re-exported so the ADK runtime can
discover the agent. The loader resolves ``app`` first, which is what activates
the observability plugin chain.

IMPORT-TIME ENVIRONMENT LOADING
-------------------------------
``app.agent`` constructs its toolsets at module scope, so configuration has to be
resolvable the instant this package is imported. ``fast_api_app`` also calls
``load_dotenv()``, but that runs *after* Python has already executed this
``__init__``, which is far too late: under
``uvicorn app.fast_api_app:app`` the container would crash on boot with
``ConfigurationError: BIGTABLE_MCP_URL is not configured``.

Loading here fixes the ordering for every entrypoint (uvicorn, ``adk web``,
pytest, ``agents-cli eval``) with one line. Real environment variables always
win -- ``load_dotenv`` does not override them -- so Agent Runtime's
``--update-env-vars`` remains the authoritative source in the cloud and the
``.env`` file is only a local-development convenience.
"""

from pathlib import Path

from dotenv import load_dotenv

# The project keeps its configuration in `app/.env`, which is NOT what
# `load_dotenv()` finds by default (it walks up from the process CWD). Load the
# package-local file by absolute path first, then fall back to the default
# search so a repo-root `.env` still works for contributors who prefer that.
load_dotenv(Path(__file__).with_name(".env"))
load_dotenv()

from . import agent  # noqa: E402  (must follow load_dotenv)
from .agent import app, root_agent  # noqa: E402

__all__ = ["agent", "app", "root_agent"]
