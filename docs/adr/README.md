# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) documenting significant architectural decisions in Unstash.

## What is an ADR?

An ADR is a lightweight document that captures a single architectural decision and its rationale. ADRs are the institutional memory of a project — they let future contributors (including future-us) understand *why* the codebase is the way it is, not just *what* it does.

See [ADR 0000](./0000-record-architecture-decisions.md) for the full rationale behind using ADRs.

## Format

Each ADR follows Michael Nygard's structure:

1. **Title** — short and descriptive
2. **Status** — Proposed, Accepted, Deprecated, or Superseded by ADR NNNN
3. **Context** — the problem and constraints
4. **Decision** — what we decided to do
5. **Consequences** — positive, negative, and neutral results

## Index

| ADR | Title | Status |
|---|---|---|
| [0000](./0000-record-architecture-decisions.md) | Record Architecture Decisions | Accepted |
| [0001](./0001-initial-stack-choices.md) | Initial Stack Choices | Accepted |
| [0002](./0002-ci-driven-deployment.md) | CI-Driven Deployment via SSH Docker Context | Accepted |
| [0003](./0003-file-based-secrets-on-vps.md) | File-Based Secrets on the VPS | Accepted |
| [0004](./0004-branch-based-environments.md) | Branch-Based Environments (development → staging, main → production) | Accepted |

## Creating a new ADR

1. Find the next available number
2. Create `NNNN-short-kebab-case-title.md` in this directory
3. Fill in the sections: Status, Context, Decision, Consequences
4. Add it to the index above
5. Commit as part of the PR that implements (or proposes) the decision

ADRs are immutable once accepted. If a decision changes, create a new ADR that supersedes the old one, and update the old one's status to "Superseded by ADR NNNN".
