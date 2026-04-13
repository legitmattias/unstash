# Docker

Container images and Compose configuration for Unstash.

## Dockerfiles

| File | Purpose | Stages |
|---|---|---|
| `backend.Dockerfile` | FastAPI backend | `builder` → `dev` (hot reload) → `runtime` (production) |
| `frontend.Dockerfile` | SvelteKit frontend | `builder` → `dev` (hot reload) → `runtime` (production) |

Both use multi-stage builds. Development overrides (`compose.dev.yaml`) target the `dev` stage for source volume mounting and hot reload. Production overrides (`compose.prod.yaml`) target `runtime` with non-root users and health checks.

## Supporting files

- `init-db.sh` — PostgreSQL init script that creates the restricted `unstash_app` user (DML only, no DDL, no superuser). Required for Row-Level Security to be effective.
- `Caddyfile.snippet` — Reference Caddy virtual host configuration for production.

## Compose files

Compose files live at the repo root:

| File | Purpose |
|---|---|
| `compose.yaml` | Base: service definitions, secrets, networks, volumes |
| `compose.dev.yaml` | Dev overrides: hot reload, exposed ports, debug logging |
| `compose.prod.yaml` | Prod overrides: resource limits, restart policies |

Usage:

```sh
# Development
docker compose -f compose.yaml -f compose.dev.yaml up --build

# Production
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

Or via Makefile: `make up` / `make down` / `make logs`.
