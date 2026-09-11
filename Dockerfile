# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Cymbal Retail Operations Coordinator Agent - Vertex AI Agent Runtime image.
#
# The build contract is dictated by Agent Runtime (Reasoning Engine): it uploads
# the source tree, builds THIS Dockerfile, and expects an HTTP server on $PORT
# serving the native ADK reasoning_engine contract. `app/fast_api_app.py`
# provides that surface (adk_api routes + A2A + the Console Playground proxy).
#
# Dependency resolution is pinned through uv.lock, which is generated against
# the PUBLIC PyPI index. Cloud Build has no line of sight to an internal
# Artifact Registry, so a lockfile carrying internal registry URLs fails the
# build with an opaque 401 - see README "Deployment" for the regeneration
# command.
#
# Runtime configuration is injected as real environment variables by
# `agents-cli deploy --update-env-vars`; nothing environment-specific is baked
# into the image (12-factor).
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Pinned so an upstream uv release cannot silently change resolution behaviour
# between a green local build and a red Cloud Build.
RUN pip install --no-cache-dir uv==0.8.13

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /code

# Copy the resolution inputs first so the dependency layer stays cached across
# application-code edits.
COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./app ./app

# --frozen: fail loudly if uv.lock has drifted from pyproject.toml rather than
# silently resolving something different from what was tested locally.
RUN uv sync --frozen --no-dev

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

# Drop privileges. The container writes nothing outside /tmp at runtime; ADK
# session and artifact state live in managed services, not on local disk.
RUN useradd --create-home --uid 1001 agent && chown -R agent:agent /code
USER agent

# Agent Runtime and Cloud Run both address the container on 8080.
EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
