# ADR 0003: File-Based Secrets on the VPS

## Status

Accepted

## Context

Unstash requires several runtime secrets: the application database password, the database migrations password, a session cookie signing key, and a Fernet key for encrypting OAuth tokens at rest. These values must be available to containers at startup but must never appear in version control or be accessible to unauthorized parties.

File-based secrets mounted at `/run/secrets/<name>` are preferred over environment variables, because environment variables leak through multiple vectors: `docker inspect`, crash dumps, log output, child process inheritance, and `/proc/<pid>/environ` readable by any process that can read the directory.

Three implementation patterns are compatible with the file-based approach:

1. **Env-var injection with CI-side storage.** GitHub Actions holds all secret values; the deploy workflow injects them into the compose command environment so compose substitutes them into container `environment:` blocks.
2. **File-based with CI-written files.** CI holds the values, writes them to the VPS filesystem as temporary files during deploy, compose mounts them, and CI optionally removes them afterwards.
3. **File-based with persistent on-VPS files.** Secret files live on the VPS at a stable location, placed once during infrastructure setup. CI never sees the values — it only triggers `docker compose up -d`, and the local daemon mounts the files into containers.

An earlier iteration of this project used pattern 1 (env-var injection). It worked but was identified as a deviation from the file-based-secrets baseline and corrected.

## Decision

Use pattern 3: file-based secrets with persistent on-VPS files.

Key properties of the design:

- Secret files live in a dedicated on-VPS directory, separated per-environment (staging and production have independent secret sets).
- The directory is owned by the deploy user account, so routine operations require no privilege escalation after initial setup.
- Directory permissions restrict host-side access to the deploy user; file permissions are relaxed enough for container processes to read the bind-mounted files.
- Docker Compose's top-level `secrets:` section with `file:` directives references the on-VPS paths. Compose mounts the files at `/run/secrets/<name>` inside containers at the standard path expected by the application.
- The application reads secrets from `/run/secrets/` via Pydantic Settings' `secrets_dir` feature — no application code change is needed regardless of which secrets mechanism is used.

CI is responsible only for building images, pushing them to GHCR, and running `docker compose pull && up -d` against the remote daemon. It never needs the secret values.

Specific paths, permissions, and operational procedures live in the operational runbooks, not in this ADR.

## Alternatives Considered

### Pattern 1: Environment-variable injection via CI

See context. Rejected as the explicit non-compliance that motivated this ADR.

### Pattern 2: CI-written secret files

CI holds the values and writes them to the VPS filesystem during each deploy.

- **Pros:** GitHub Actions UI becomes the single source of truth for secrets, with built-in rotation UI.
- **Cons:** CI gains write access to a sensitive directory on the VPS. A compromised GitHub token grants the attacker full production secret access. Every deploy potentially rewrites the secret files. CI credentials are a larger blast radius than necessary.

Rejected because the blast radius of GitHub access should not include "production application secrets." Pattern 3 grants CI only what it needs: the ability to trigger restarts.

### External secrets manager (HashiCorp Vault, Infisical, Bitwarden Secrets Manager)

Run a dedicated secrets manager service; containers fetch secrets at startup or via a sidecar.

- **Pros:** Centralized secret management with rotation workflows, audit logs, fine-grained access control, and support for dynamic secrets.
- **Cons:** Adds an entirely new service to operate, secure, back up, and monitor. Introduces a runtime dependency that must be available for the application to start. Complexity vastly exceeds the project's current needs.

Not rejected outright — an attractive upgrade path once the project grows past a single application on a single VPS.

### Docker Swarm secrets

Use Docker Swarm's native secrets mechanism with automatic distribution and encryption at rest on the Raft log.

- **Pros:** Purpose-built for this use case; handles rotation, encryption, distribution.
- **Cons:** Requires running in Swarm mode, which is otherwise unused. Adds Swarm's operational overhead (cluster initialization, manager/worker distinction) for a single-node deployment.

Rejected because it asks the project to adopt a second orchestration layer (Swarm alongside Compose) for one feature.

## Consequences

**Positive:**

- Application secrets never leave the VPS. CI compromise does not expose them.
- Secret rotation is a local host operation — no CI secret update, no commit required.
- The separation of infrastructure secrets (in CI) from application secrets (on VPS) scales naturally to multi-operator setups: future developers get CI access for deploys without gaining production secret access.

**Negative:**

- Secrets are not in a shared UI or in a password manager by default. An external backup (password manager or equivalent) is recommended operator discipline.
- Initial setup requires direct host access to the VPS to place the files. Rotation also requires direct host access, which is more friction than editing a value in a web UI. Both are acceptable given these are rare operations.
- If the VPS is destroyed without an external backup of the secret values, application secrets are lost and must be rotated. The password manager backup mitigates this.

**Neutral:**

- Docker Compose with a remote daemon validates secret paths client-side and emits a warning when the file is not present on the client. The actual bind mount happens daemon-side and works correctly. The warning is harmless but noisy.

## Runbook

Operational procedures for initial setup and rotation belong in `docs/runbooks/` (not yet written).

## References

- ADR 0002 — CI-driven deployment (explains why CI has SSH access in the first place)
- Pydantic Settings documentation — `secrets_dir` configuration
- Docker Compose reference — top-level `secrets:` key and service-level `secrets:` references
