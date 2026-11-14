# Scripts

Helper scripts for development, deployment, and operations.

Scripts that run routinely should be exposed via the top-level `Makefile` targets rather than called directly. This directory is for the implementations.

## Scripts

- `host-bootstrap.sh` — prepares a fresh host to run the compose stack (Docker, reverse proxy, identities, directories, firewall, backup timer). Run as root on the host; see `docs/runbooks/host-setup.md`.
- `backup-database.sh` — dumps one environment's database to a dated, verified file and prunes old dumps. Installed on the host by the bootstrap script and run nightly by a systemd timer.

## Planned scripts

- `dev-setup.sh` — one-time local dev setup (generate secrets, start stack, run migrations)
- `generate-secret.sh` — generate a specific secret file using the correct format
- `migrate.sh` — wrapper around Alembic with Docker Compose
- `deploy.sh` — deployment helper (uses Docker context for production)
- `restore.sh` — restore a specific backup (wraps the restore runbook)

All scripts should:

- Be idempotent where possible
- Fail fast with clear error messages
- Print what they're about to do before doing it
- Include a `--help` flag documenting usage
- Use `set -euo pipefail` in bash (fail on any error, undefined variable, or pipeline failure)
