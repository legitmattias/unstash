# ADR 0000: Record Architecture Decisions

## Status

Accepted

## Context

As Unstash grows, we will make many architecture decisions — choice of libraries, data models, deployment strategies, API shapes, security trade-offs. Without a record of these decisions, future maintainers (including future-us) will wonder why things are the way they are, and may repeat debates that have already been settled.

We need a lightweight, durable way to record:

- The decision itself
- The context in which it was made
- The alternatives that were considered
- The consequences of choosing this path

The record should live with the code, be readable in plain text, and survive team changes and tool migrations.

## Decision

We will use **Architecture Decision Records (ADRs)** as described by Michael Nygard in [Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

Each ADR:

- Lives in `docs/adr/` in the source code repository
- Is named `NNNN-short-kebab-case-title.md`
- Is numbered sequentially starting from 0000
- Follows the structure: Title → Status → Context → Decision → Consequences
- Is immutable once accepted (changes are recorded by creating a new ADR that supersedes the old one)
- Is written at the time the decision is made, not retrofitted

## Consequences

**Positive:**
- Decisions are traceable and auditable
- New contributors can understand *why*, not just *what*
- Debates about settled decisions can be closed by pointing to the ADR
- Writing an ADR forces clear thinking about alternatives and consequences
- ADRs form a history of how the architecture evolved

**Negative:**
- Small overhead for each significant decision
- Requires discipline to actually write them

**Neutral:**
- ADRs are lightweight Markdown files — no tooling required

## Status changes

ADRs can be in one of these states:

- **Proposed** — under discussion
- **Accepted** — decided and in effect
- **Deprecated** — no longer recommended but still in effect somewhere
- **Superseded by ADR NNNN** — replaced by a newer decision

If an ADR is superseded, the new ADR explicitly references the one it replaces, and the old ADR's status is updated to "Superseded by ADR NNNN".
