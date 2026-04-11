# Scripts

Helper scripts for development, deployment, and operations.

Scripts that run routinely should be exposed via the top-level `Makefile` targets rather than called directly. This directory is for the implementations.

## Planned scripts

- `dev-setup.sh` — one-time local dev setup (generate secrets, start stack, run migrations)
- `generate-secret.sh` — generate a specific secret file using the correct format
- `migrate.sh` — wrapper around Alembic with Docker Compose
- `deploy.sh` — deployment helper (uses Docker context for production)
- `backup.sh` — trigger a manual backup via pgBackRest
- `restore.sh` — restore a specific backup (wraps the restore runbook)

All scripts should:

- Be idempotent where possible
- Fail fast with clear error messages
- Print what they're about to do before doing it
- Include a `--help` flag documenting usage
- Use `set -euo pipefail` in bash (fail on any error, undefined variable, or pipeline failure)
