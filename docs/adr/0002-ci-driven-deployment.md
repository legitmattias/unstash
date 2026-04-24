# ADR 0002: CI-Driven Deployment via SSH Docker Context

## Status

Accepted

## Context

Unstash needs a way to deploy the application to its production VPS reliably and reproducibly. The initial plan documented in ADR 0001 and the master plan assumed deployment would be performed from the developer's local machine using Docker context (`docker context use production`) to target the remote Docker daemon over SSH.

While this is a valid approach, it has drawbacks:

- **Not reproducible.** Deploys depend on the state of the operator's local machine — which code branch is checked out, which Docker credentials are loaded, which environment variables are set.
- **No audit trail.** Deploys happen silently from a laptop. There's no shared record of who deployed what, when, or from which commit.
- **No automation of quality gates.** Running tests, linting, and type checking before each deploy relies on the operator remembering to run them.
- **Does not scale past one person.** A second developer would need access to the VPS Docker daemon, matching Docker CLI setup, and matching secrets — or they can't deploy.
- **Secrets live on the operator's machine**, not in a system that can be audited or rotated centrally.

## Decision

Deploy via GitHub Actions workflows that use SSH-based Docker context to run `docker compose` commands on the remote VPS daemon.

Implementation:

- GitHub Actions workflow `deploy.yaml` builds container images on the runner, pushes them to GitHub Container Registry (GHCR), and runs `docker compose pull && up -d` against the remote daemon via `DOCKER_HOST=ssh://${DEPLOY_USER}@${DEPLOY_HOST}`.
- SSH authentication uses a dedicated ed25519 deploy key generated specifically for CI, stored as `DEPLOY_SSH_KEY` in GitHub Actions secrets.
- The CI runner never stores code on the VPS. Its only effects on the VPS are:
  1. Pulling new images from GHCR into the local Docker daemon.
  2. Recreating containers with the new images.
  3. Bind-mounting pre-existing secret files into those containers (see ADR 0003).

All deploys therefore flow through a logged, reproducible CI run tied to a specific Git commit.

## Alternatives Considered

### Local Docker context (original plan)

Described above. Rejected because it sacrifices reproducibility, audit trail, and multi-operator readiness to save the setup cost of a deploy workflow. That trade-off was explicit in the original plan but revisited once the GitHub Actions infrastructure was already in place for CI anyway.

### Push-based deploy agent on the VPS

Install a small agent on the VPS that polls GitHub for new releases (or watches a webhook) and runs `docker compose pull && up -d` when a new tag appears. This inverts the control direction so CI never needs SSH access.

- **Pros:** VPS doesn't need to expose SSH to the internet for CI; deploys are pull-initiated rather than push-initiated.
- **Cons:** Adds a new component to operate. Harder to debug when deploys fail. The audit trail is split between GitHub (what was released) and VPS logs (what was pulled and when).

Not rejected outright — revisit if SSH-from-CI becomes a security concern or if deploy orchestration grows to multiple targets.

### Kubernetes / orchestrated deployment

Deploy to a Kubernetes cluster instead of a Docker Compose stack on a single VPS.

- **Pros:** Industry-standard, scalable, declarative, supports blue-green and canary deploys natively.
- **Cons:** Significant operational complexity for a single-VPS deployment. The cost-benefit doesn't clear the bar until the project grows past one machine and one service group. Docker Compose on a single VPS remains appropriate until that threshold.

## Consequences

**Positive:**

- Deploys are reproducible, logged, and tied to specific commits.
- CI quality gates (lint, type check, tests, security scan) must pass before the deploy job runs.
- Multiple operators can deploy without each needing local Docker context setup.
- Secrets management is cleanly separated: CI holds only infrastructure secrets (SSH key); application secrets live on the VPS (see ADR 0003).
- Rollback is a matter of re-running the workflow with an older image tag.

**Negative:**

- Requires ongoing maintenance of the deploy workflow as GitHub Actions and Docker evolve.
- Deploy is coupled to GitHub availability — if GitHub is down, deploys are blocked until it recovers. Acceptable given GitHub's uptime record and the fact that the VPS keeps running the previously deployed version.
- Each deploy uses GitHub Actions minutes. At current volume this is well within free tier.

**Neutral:**

- CI runner performs the image builds. Build artifacts live in GHCR. The VPS never does `docker build` — only `docker pull` and `docker run`.

## References

- ADR 0001 — Initial stack choices (this ADR supersedes the "Docker context from developer machine" language in the infrastructure section)
- ADR 0003 — File-based secrets on the VPS
- GitHub Actions docs on `DOCKER_HOST`
- Docker docs on SSH contexts
