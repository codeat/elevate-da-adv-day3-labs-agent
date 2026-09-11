# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Cymbal Retail Operations Coordinator Agent
# Multi-stage, non-root, distro-slim container image.
# All runtime configuration arrives via environment variables (12-factor).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 1001 agent
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=agent:agent app/ ./app/
COPY --chown=agent:agent tools.yaml ./tools.yaml

USER agent
EXPOSE 8000

# Fail fast when mandatory environment properties are absent.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/list-apps',timeout=4)" || exit 1

CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8000", "."]
