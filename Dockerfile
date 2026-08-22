# syntax=docker/dockerfile:1

# Base image is overridable so a digest can be pinned without editing this file:
#
#   docker buildx imagetools inspect python:3.12-slim-bookworm \
#     --format '{{json .Manifest.Digest}}'
#   docker build --build-arg PYTHON_IMAGE=python@sha256:<digest> .
#
# The tag below is the default. It has NOT been replaced with a digest here,
# because resolving one requires a registry round trip -- run the command above
# and commit the result, or pass it per build. A tag can move under you.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm


FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency metadata first, so a source-only change reuses the wheel layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .


FROM ${PYTHON_IMAGE} AS runtime

ARG VERSION=0.1.0
ARG REVISION=unknown

LABEL org.opencontainers.image.title="FinOpsAI" \
      org.opencontainers.image.description="Cost attribution for AI workloads" \
      org.opencontainers.image.source="https://github.com/Garvil007/Finops" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    METRICS_PORT=9100

# Unprivileged, no login shell, no home-directory writes needed at runtime.
RUN groupadd --system --gid 10001 finops \
 && useradd --system --uid 10001 --gid finops --create-home --shell /usr/sbin/nologin finops

WORKDIR /app
COPY --from=builder --chown=root:root /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root migrations ./migrations

USER finops

EXPOSE 9100

# Checks the default command's surface: the worker's Prometheus endpoint. The
# api service overrides this in compose, because it serves /healthz on 8000
# instead. urllib keeps the image free of curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; \
url=f\"http://localhost:{os.environ.get('METRICS_PORT','9100')}/metrics\"; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)" || exit 1

CMD ["python", "-m", "finopsai.collectors"]
