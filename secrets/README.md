# Secrets Directory

This directory holds **real secret files** for local development and (on the production server) for production. Everything in this directory except this README and `.gitkeep` is gitignored.

## Principles

- **Never commit secrets to git.** Enforced by gitleaks pre-commit hook, but also your own discipline.
- **Never use environment variables for secrets** inside containers. Environment variables leak via `docker inspect`, logs, crash dumps, and child processes. Use file-based Docker Compose secrets mounted at `/run/secrets/<name>`.
- **Different secrets per environment.** Dev, staging, and production each have their own set.
- **Document what's needed.** If you add a new secret, add an entry below so future-you (and collaborators) know what to generate.

## Required secrets

Each secret is a single file containing only the secret value, with no trailing newline by convention.

| File | Purpose | How to generate |
|---|---|---|
| `database_password.txt` | PostgreSQL password for the `unstash_app` user | `openssl rand -base64 32` |
| `database_migrations_password.txt` | PostgreSQL password for the `unstash_migrations` user (schema owner) | `openssl rand -base64 32` |
| `redis_password.txt` | Redis password | `openssl rand -base64 32` |
| `session_secret.txt` | Secret key for session cookie signing | `openssl rand -base64 64` |
| `encryption_key.txt` | Fernet key for encrypting OAuth connector tokens at rest | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `jina_api_key.txt` | Jina AI API key for embeddings | Get from https://jina.ai |
| `mistral_api_key.txt` | Mistral AI API key for cluster labeling | Get from https://console.mistral.ai |

Additional secrets added as the project grows (Google OAuth, Stripe, etc.) should be documented here as they're introduced.

## File permissions

```bash
chmod 700 secrets/
chmod 600 secrets/*.txt
```

## Production

Production and staging secrets live on the deployment host, outside of CI. Docker Compose references them via the `file:` directive. CI never sees the values — it only triggers image pulls and container restarts on the remote daemon. See the relevant ADR for the decision rationale.

## Rotation

Each secret has or should have a rotation runbook in `docs/runbooks/rotate-<secret>.md`. Rotate secrets periodically and on suspicion of compromise.
