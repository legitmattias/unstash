# Docker

Container images and Docker Compose configuration for Unstash.

## Contents

- `backend.Dockerfile` — multi-stage build for the FastAPI backend
- `frontend.Dockerfile` — multi-stage build for the SvelteKit frontend
- `worker.Dockerfile` — worker image including document parsing and ML dependencies
- `Caddyfile.snippet` — Caddy virtual host configuration

Compose files live at the repository root: `compose.yaml`, `compose.dev.yaml`, `compose.prod.yaml`.

## Principles

- Multi-stage builds: separate build and runtime images
- Non-root user inside containers
- Minimal runtime images (distroless where practical)
- Health checks defined in Dockerfiles and Compose
- Graceful shutdown with SIGTERM handling
- File-based Docker Compose secrets mounted at `/run/secrets/` (never environment variables for sensitive values)

## Getting started

See the top-level [README](../README.md) for the development workflow.
