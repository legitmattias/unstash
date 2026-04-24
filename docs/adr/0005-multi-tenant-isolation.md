# ADR 0005: Multi-Tenant Isolation — Application Filter + Row-Level Security

## Status

Accepted

## Context

Unstash is a multi-tenant SaaS. Every org's documents, embeddings, search history, and audit records must be inaccessible to every other org. A cross-tenant data leak is the worst possible bug: legally, reputationally, and for the product's credibility in contexts like BRFs and professional services firms where confidentiality is the baseline expectation.

Two mechanisms can enforce tenant scoping, and the question is which to use:

1. **Application-level filtering** — every query in the repository layer explicitly filters by `org_id`. SQLAlchemy query builders, hand-written SQL, and ORM relationships all apply the filter. Correctness depends on discipline and code review.
2. **Database-level Row-Level Security (RLS)** — policies on each tenant-scoped table restrict which rows any role can SELECT, INSERT, UPDATE, or DELETE. A PostgreSQL session variable (`app.current_org_id`) is set at the start of each transaction to identify the current tenant. The database enforces the scope regardless of what the application code does.

There is also the question of **how the active org is determined per request** — ambient session state, or explicit per-request URL/header identification.

Multi-tenant isolation is treated as non-negotiable for this project: a cross-tenant data leak is the worst possible bug. M2 is the milestone where the enforcement model is built in from the start. Retrofitting RLS onto a working system is notoriously leaky; adding an application filter late is similarly error-prone. The decision must be made now.

## Decision

Adopt a **defense-in-depth** model with three mutually reinforcing layers:

### 1. Application-level filter

Every repository method that queries an org-scoped table includes `org_id` in its `WHERE` clause. SQLAlchemy query construction helpers make this the default — a repository that queries without an `org_id` filter is a bug.

### 2. Row-Level Security in PostgreSQL

Every org-scoped table has RLS enabled and at least one policy of the form:

```sql
CREATE POLICY tenant_isolation ON <table>
    USING      (org_id = current_setting('app.current_org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.current_org_id')::uuid);
```

`USING` filters what can be read (also governs the visibility of rows eligible for UPDATE/DELETE). `WITH CHECK` verifies that rows being written — INSERT or UPDATE — have an `org_id` matching the current session. Both clauses are always present on write-capable policies; omitting `WITH CHECK` would allow INSERTs into the wrong tenant that become invisible "orphan" rows, which is a subtle but real leak surface during migrations and debugging.

Two database roles carry distinct privileges:

- `unstash_app` — non-superuser, `NOBYPASSRLS`. All application queries use this role. Cannot escape RLS.
- `unstash_migrations` — schema owner, `BYPASSRLS`. Used only by Alembic. Never used by the application at runtime.

Postgres superusers bypass RLS by design. `unstash_app` is explicitly not a superuser. Startup checks verify this on boot and fail loudly if the role was misconfigured.

### 3. Per-request org identity via the URL path

The active org for a request is determined by the URL, not by ambient session state. Tenant-scoped endpoints are namespaced under `/orgs/{org_id}/...`. FastAPI middleware:

1. Extracts `org_id` from the path.
2. Validates that the authenticated user has an active membership in that org.
3. Opens a database transaction.
4. Runs `SET LOCAL app.current_org_id = '<org_id>'` on the transaction.
5. Hands off to the route handler.
6. Commits or rolls back at exit.

`SET LOCAL` scopes the variable to the current transaction only — critical for connection pooling, where a connection may be handed to a different tenant's request on its next use. `SET` or `SET SESSION` would leak across requests through the pool and is forbidden.

For Taskiq workers, an equivalent context manager opens a transaction and sets `app.current_org_id` at the start of every job that touches tenant data. Jobs that span multiple tenants are rare and handled by explicitly opening one transaction per tenant.

## Alternatives Considered

### RLS only, no application filter

Trust RLS as the single source of truth. Drop the `org_id` filter from repository queries.

- **Pros:** Less code, no "two filters that could drift." Single place to change the policy. Reasoning about tenant scoping is localized to the database layer.
- **Cons:** RLS policy bugs become the only thing standing between tenants. Policy bugs are silent — no rows match, which looks identical to "no matching rows exist." Debugging why a query returns zero rows requires understanding both the query and the active RLS context, increasing cognitive load. ORM-generated subqueries, JOINs, and views have subtle RLS interactions that are easy to miss in review. Middleware failures to `SET LOCAL` produce errors, but the error surface varies by query shape.

Rejected because the cost of the application filter is nearly zero — the `(org_id, ...)` composite index makes it an index-prefix check, not an extra scan — and it provides an independent second mechanism. Two independent mechanisms must fail together for a leak; either alone is insufficient for exfiltration.

### Application filter only, no RLS

Rely on disciplined application code. Skip RLS entirely.

- **Pros:** No `SET LOCAL` lifecycle to manage. No two database roles. Simpler operational model. One place to reason about scoping.
- **Cons:** A single bug or missing filter in one code path leaks everything it touches, silently. Raw SQL, ad-hoc `psql` sessions for debugging production, future reporting tools, background scripts — all bypass the filter and expose all tenants. No protection against SQL injection that successfully reaches the database. No protection against an attacker who gains application-level read access. Every new developer and every new code path becomes a tenant-isolation review.

Rejected because the product's positioning (BRF confidentiality, GDPR, professional services secondary market) makes "one bug = full leak" an unacceptable failure mode. The cost of RLS is real but bounded; the cost of a leak is existential.

### Schema-per-tenant

Each org gets its own PostgreSQL schema. Queries specify the schema explicitly or via `search_path`.

- **Pros:** Hard isolation by default — no shared tables to leak across. Easy per-tenant backup and delete.
- **Cons:** Migrations multiply across schemas, becoming operationally painful at tenant-count scale (N migrations run N times). Cross-tenant analytics (platform health, aggregate usage) require union queries across all schemas. Vector and BM25 indexes are per-schema, losing cross-tenant cache locality. PostgreSQL's `search_path` is ambient state — the exact problem `SET LOCAL app.current_org_id` is designed to solve for RLS, now moved into the schema layer with fewer safety nets.

Rejected because Unstash's tenant count will grow into hundreds or thousands, and schema-per-tenant doesn't scale operationally. Shared-schema + RLS is the mainstream pattern for multi-tenant SaaS of this shape.

### Database-per-tenant

Each org gets its own PostgreSQL database.

- **Pros:** The hardest isolation — tenants share nothing at the data layer.
- **Cons:** Operationally extreme at tenant count. New-tenant provisioning becomes a heavy process. Upgrades, backups, and schema migrations multiply. Shared connection pools don't work. Infrastructure cost per tenant is high.

Rejected for the same scaling reasons as schema-per-tenant, more so.

### Ambient-session active-org (instead of per-request URL)

User picks an active org at login; session state carries it. Endpoints do not include `org_id` in the path.

- **Pros:** Shorter URLs. No per-request parameter to pass. Matches some existing SaaS conventions.
- **Cons:** The active tenant becomes ambient state that is invisible in URLs, logs, and stored API traces. Bugs where "session has the wrong active org" produce cross-tenant operations without any path evidence. Switching org requires an explicit "switch context" action; multi-tab workflows on different orgs are confusing.

Rejected because visible-in-the-URL tenant identity is easier to audit, log, and reason about. Path-based routing also makes path-level authorization (middleware lookup of membership) the natural enforcement point.

## Consequences

**Positive:**

- Two independent enforcement mechanisms. A cross-tenant leak requires *both* the application filter to be missing *and* the RLS policy or session variable to be wrong. The probability of both failing in the same code path is meaningfully lower than either alone.
- RLS catches the "someone ran raw SQL in production or in a worker without the proper abstraction" class of mistake. The application filter catches ORM surprises (subqueries, JOINs, materialized views) where RLS might behave unexpectedly.
- `SET LOCAL` confines the session variable to one transaction, safe under connection pooling.
- `WITH CHECK` on write policies closes the orphan-row hole silently created by `USING`-only policies.
- Per-request URL-based org identity makes the active tenant inspectable in logs, traces, and API contracts. No hidden state.
- Two DB roles cleanly separate "who can alter the schema" from "who can read/write data." The application role physically cannot bypass RLS.

**Negative:**

- Two filters can drift over time. If the application filter is stricter than the RLS policy, queries may silently return fewer rows than expected. The adversarial test suite includes meta-tests that surface this kind of drift.
- Every transaction needs `SET LOCAL app.current_org_id` set before the first query. Middleware does this for HTTP requests; worker jobs have an equivalent context manager. Missing either produces RLS errors that may be obscure to debug.
- `current_setting('app.current_org_id')` in the RLS policy raises an error if never set, rather than quietly returning zero rows. This is actually the safer failure mode but tests need to handle it explicitly.
- Two database passwords to provision per environment (`unstash_app` and `unstash_migrations`).

**Neutral:**

- `(org_id, ...)` composite indexes are needed on every tenant-scoped table for the application filter to be fast. These would be needed for RLS query plans anyway, so this is table stakes rather than overhead.
- RLS policies are part of the schema and version-controlled via Alembic migrations, same as indexes and constraints.

## Verification

The M2 adversarial test suite includes:

- A test that queries each org-scoped table as `unstash_app` without setting `app.current_org_id` and asserts zero rows / raised error for every path.
- A test with two orgs in the database; queries running as Org A never return Org B's rows even when explicit SQL tries `WHERE org_id = <Org B's id>`.
- A meta-test that scans `pg_class` to verify every table matching the tenant-scoped naming pattern has `rowsecurity` enabled and at least one policy.
- A test that attempts INSERT with a mismatched `org_id` and confirms the `WITH CHECK` clause rejects it.
- A test that confirms `unstash_app` is `NOBYPASSRLS` and not a superuser, as a regression guard.

These tests run in CI against a real PostgreSQL (via testcontainers), not a mocked database. Tenant isolation is one of the few properties where mock-based testing is actively harmful.

## References

- PostgreSQL documentation on Row-Level Security — the authoritative reference for policy syntax and semantics.
