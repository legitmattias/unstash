# ---------------------------------------------------------------------------
# Stage: builder — install dependencies and source
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

# libmagic1 is needed at runtime by python-magic (used for content-based
# MIME detection during ingestion). Installed in the builder so both the
# dev image (which extends builder) and the runtime image (which only
# copies the venv) work — the runtime stage installs it again on its
# own slim base.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (layer caching — deps change less than source).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself.
# --no-editable bakes the source into site-packages so the runtime stage
# doesn't need /app/src. The dev stage below re-syncs with dev deps, which
# is compatible with runtime's non-editable install.
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

# Alembic configuration and migration scripts. Needed at runtime so the
# `alembic` command works inside the container. The dev compose file
# bind-mounts these over the COPY'd versions so local edits are picked up
# without a rebuild; runtime uses the COPY'd files as-is.
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic

# ---------------------------------------------------------------------------
# Stage: dev — local development with hot reload
# ---------------------------------------------------------------------------
FROM builder AS dev

# Re-install with dev dependencies for testing and linting inside container.
RUN uv sync --frozen

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "unstash.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# Stage: runtime — minimal production image
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# libmagic1 is required at runtime by the python-magic MIME detector.
# Without it, ``import magic`` works but every call raises at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmagic1 && \
    rm -rf /var/lib/apt/lists/*

# Pin the runtime UID/GID to 1000 so host bind-mounts (e.g. the
# documents data volume on the VPS, owned by the operator's local
# uid 1000) are writable by the container without chmod gymnastics.
# A system account is still appropriate — no login shell, no home —
# but we make the numeric uid explicit and stable across rebuilds.
# The home directory matters here because the model caches we
# warm below land under $HOME — so we DO create it and set it to /app.
RUN groupadd --system --gid 1000 unstash && \
    useradd --system --uid 1000 --gid unstash --home-dir /home/unstash --create-home unstash && \
    mkdir -p /home/unstash/.cache/huggingface && \
    chown -R unstash:unstash /home/unstash/.cache

WORKDIR /app

COPY --from=builder --chown=unstash:unstash /app/.venv /app/.venv
COPY --from=builder --chown=unstash:unstash /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=unstash:unstash /app/alembic /app/alembic

ENV PATH="/app/.venv/bin:$PATH"

USER unstash

# HuggingFace + Docling model cache. Mount point pre-created with
# unstash ownership in the useradd RUN block above so the named
# docker volume mounted here at runtime is writable by the worker.
ENV HF_HOME=/home/unstash/.cache/huggingface

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/ready')"]

CMD ["uvicorn", "unstash.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
