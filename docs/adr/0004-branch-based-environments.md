# ADR 0004: Branch-Based Environments (development → staging, main → production)

## Status

Accepted (amended 2026-04-19 — see Amendment section below)

## Context

With continuous deployment wired to Git (see ADR 0002), the project needs a convention for which branch triggers which environment. The earliest version of the deploy workflow used a single `main` branch: push to `main` auto-deployed to staging, and production deployment required manual `workflow_dispatch` invocation with an environment parameter.

This worked but had a subtle failure mode: staging and production could silently drift. After a period of pushing changes to main (which updated staging) but forgetting to manually promote to production, staging might be several commits ahead of what's actually live. The question "what is currently in production?" required looking at GitHub Actions run history rather than the Git log.

The codebase needs a convention where the current state of a long-lived branch directly corresponds to what is running in each environment.

## Decision

Adopt a two-branch model:

- **`development`** — the integration branch. Pull requests from feature branches target `development`. When a PR is merged, the deploy workflow runs CI and, on success, deploys to the staging environment.
- **`main`** — the production-is-here branch. When `development` is stable and ready to ship, merge `development` into `main` via a pull request. The merge triggers the deploy workflow, which runs CI again and deploys to production.

`main` HEAD always equals what is running in production. `git log main..development` always shows the changes that are in staging but not yet shipped.

Branch protection on both branches:

- Requires a pull request to merge (no direct pushes).
- Requires CI to pass before merge.
- `main` additionally requires a linear history to keep the deploy log readable.

A `workflow_dispatch` trigger is retained on the deploy workflow as an escape hatch for re-deploying the current branch head without a new commit (useful after manual container cleanup or to pull a specific image tag for rollback).

## Alternatives Considered

### Single-branch with manual promote (original approach)

`main` is the only long-lived branch. Pushes to `main` auto-deploy to staging. Production deploys via `workflow_dispatch`.

- **Pros:** Simplest possible branch structure. No promotion merges.
- **Cons:** Staging and production drift over time. Operators forget to promote. "What's in production?" is not answerable from the Git log.

Rejected because the promote-to-production step is an event worth tracking in Git, not in workflow run history.

### Tag-based production deploys

`main` auto-deploys to staging; pushing a Git tag auto-deploys to production. Tags double as version numbers.

- **Pros:** Production state maps to named versions (v0.3.1, v0.3.2). Rollback is a matter of pushing or re-pushing an old tag.
- **Cons:** Requires consistent tagging discipline. Adds a manual step between "staging verified" and "production deployed." No longer true that `main` HEAD equals production.

Not rejected on principle — this is a valid alternative model, and the project may adopt it later if version numbers become important for external communication (changelog, customer notifications). For now, tracking production via a branch head is simpler and gives the same guarantees.

### GitFlow (develop / main / release / hotfix branches)

The full GitFlow model with dedicated release and hotfix branches.

- **Pros:** Rich semantic model for complex release processes.
- **Cons:** Overkill for a project that does continuous deployment, not scheduled releases. Release branches only make sense when shipping to production is a coordinated, time-bound event.

Rejected because Unstash ships continuously, not in batched releases.

### Environment branches (feature → staging → main)

Every environment has a branch. Changes flow from staging branch to main branch by merge.

- **Pros:** Each environment has an unambiguous branch.
- **Cons:** Merging between long-lived environment branches creates messy Git history. Encourages merging rather than fast-forwarding. Diverged branches are common. Generally considered an antipattern in modern Git workflows.

Rejected because the problem it solves (environment state tracking) is also solved by the simpler two-branch model without the downsides.

## Consequences

**Positive:**

- The answer to "what is in production?" is `git log -1 main`. The answer to "what is on staging?" is `git log -1 development`. These are never ambiguous.
- Diffing `main..development` shows exactly what is staged but not yet shipped.
- Promotion to production is a deliberate action (a PR) rather than a remembered one (`workflow_dispatch`). The PR body is where release notes go.
- Rollback to a previous production state is: revert the merge on `main`, which triggers the deploy workflow.
- Branch protection enforces CI on both branches uniformly.

**Negative:**

- Every change to production requires two pull requests (feature → development, then development → main). When both PRs are reviewed by the same person, this is some overhead. The overhead is the entire point of the model — the development → main PR is the "I am shipping this" action.
- Hotfixes that must bypass staging need a convention: cherry-pick from a feature branch to both `development` and `main`, or hotfix directly on `main` followed by merging `main` back into `development`. The latter is preferred; see runbook (to be written).

**Neutral:**

- CI pipeline runs twice per change (once on PR to development, once on PR to main). This is correct — production should never ship code that hasn't been validated in the production-target branch context.

## Operational Notes

- The `development` branch is the default branch on GitHub. New PRs target it by default.
- Opening a PR from a feature branch to `main` directly is allowed but discouraged — it bypasses the staging gate. Reserve for genuine hotfixes.
- After merging `main` back with a hotfix, immediately open a follow-up PR merging `main` into `development` to keep them aligned.

## Amendment — 2026-04-19

When implementing branch protection rulesets on GitHub, the original policy ("branch protection on both branches: no direct pushes") was softened. The revised policy is:

- **`main` is protected.** GitHub ruleset `main-protected`: require PR, require passing CI status checks, require conversation resolution, block force pushes, block deletions. Zero required approvals at present — the PR itself is the record of intent. Approval requirements can be added later without changing the branch model.
- **`development` is not branch-protected.** Direct pushes are allowed. CI still runs on every push because the CI workflow triggers on push to `development`, but the deploy step is gated on CI success within the workflow itself.

**Rationale for the relaxation:** the original model treated staging as something to protect against accidents. In practice, local development and staging together form the "playground" tier — the place where breaking things is the point. Production is the only environment where mistakes have real blast radius. Forcing a PR for every change to `development` added friction without adding safety; the CI gate inside the deploy workflow already prevents broken code from reaching staging containers.

**What this changes:**

- Feature work can still flow through PRs to `development` when review is wanted, but a direct push to `development` is no longer a policy violation for small or exploratory changes.
- The `development → main` promotion PR remains the binding "I am shipping this" action.
- The invariant "`main` HEAD equals production" is preserved.
- Hotfixes bypassing staging remain discouraged but possible via PR directly to `main`.

ADR 0004 is not superseded — the two-branch environment model stands. Only the enforcement on `development` is relaxed.

## References

- ADR 0002 — CI-driven deployment (defines what "deploy" means)
