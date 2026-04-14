# ---------------------------------------------------------------------------
# Stage: builder — install dependencies and source
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (layer caching — deps change less than source).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself.
COPY backend/src ./src
RUN uv sync --frozen --no-dev

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

RUN groupadd --system unstash && \
    useradd --system --gid unstash --home-dir /app --no-create-home unstash

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"

USER unstash

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]

CMD ["uvicorn", "unstash.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
