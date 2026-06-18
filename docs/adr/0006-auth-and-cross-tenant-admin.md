# ADR 0006: Authentication, Session Storage, and Cross-Tenant Admin

## Status

Accepted

## Context

The M2.5 milestone introduces authentication and the first protected routes. Two related questions need committing to before code is written:

1. **How does a user authenticate, and how is the session represented?** Cookie? JWT? Database-backed? Stateless?
2. **How do superuser-gated routes that legitimately need to read or write across tenants interact with the Row-Level Security policies established in ADR 0005?**

ADR 0005 fixed the tenant-isolation model: every org-scoped table has RLS, every request runs as the `unstash_app` role with `app.current_org_id` set, and every query in the repository layer also filters by `org_id`. This is excellent for the 95% of requests that belong to a single tenant. It is hostile to the 5% that don't — operator-managed user CRUD, cross-org membership management, audit-log inspection, and (later) token administration. These routes inherently span tenants.

The two questions are addressed together because both pieces of code land together in M2.5-A, and the design of one constrains the other.

## Decision

### 1. Authentication — FastAPI-Users with database-backed cookie sessions

Use the [FastAPI-Users](https://fastapi-users.github.io/fastapi-users/) library, configured with:

- **Password hashing**: Argon2id, via `pwdlib`. No fallback algorithms. Argon2id is the OWASP-recommended modern password hash and is what serious 2025-era stacks ship by default.
- **Session transport**: `CookieTransport`. The cookie is `HttpOnly`, `Secure` in production and staging, `SameSite=Lax`, with a 7-day max-age. Name `unstash_session`.
- **Session storage**: server-side, via FastAPI-Users' `DatabaseStrategy` over an `AccessToken` table (`token VARCHAR(43) PK`, `created_at TIMESTAMPTZ`, `user_id UUID FK CASCADE`). The cookie value is the token; the application looks up the row at every request to validate the session.

The session is therefore **stateful**, not a JWT. The motivation is server-side invalidation: a logout, a password change, or a "kill all my sessions" operator action takes effect on the next request. With stateless JWTs, the only way to invalidate before expiry is to either keep a deny-list (which converts the design back to stateful, only worse) or to accept that a leaked token is valid until expiry.

For operator-managed accounts only in this milestone — no self-signup endpoint. Account creation goes through the superuser-gated admin routes (see below). Self-signup arrives in a later milestone before public launch and will reuse the same hashing and session machinery.

### 2. Cross-tenant admin operations — separate Postgres role with `BYPASSRLS`

Superuser-gated routes that operate across tenants connect to PostgreSQL as a **third** role, `unstash_admin`, which has `LOGIN`, `BYPASSRLS`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and no schema ownership. The role gets DML privileges on every table via `ALTER DEFAULT PRIVILEGES` in `docker/init-db.sh`, mirroring how `unstash_app` gets its grants. The application opens a separate SQLAlchemy `AsyncEngine` bound to this role and exposes a `get_admin_session` dependency that admin routes use.

`unstash_admin` is created on first boot by `init-db.sh`. The password is a Docker Compose file-based secret at `/run/secrets/database_admin_password`, following the same pattern as `database_password` and `database_migrations_password` (ADR 0003).

Admin routes are gated at the application layer by FastAPI-Users' `current_user(active=True, superuser=True)` dependency. The route handler can only reach the admin engine because `current_superuser` runs first; the wiring is explicit so a reviewer can see at a glance that an admin route uses the admin engine.

## Alternatives considered

### Auth alternatives

- **Stateless JWT, no DB lookup.** Faster (no DB roundtrip per request), but server-side invalidation requires a deny-list table, at which point the request DOES hit the DB and the supposed advantage is gone. Logout becomes "the cookie is still valid; we just hope the browser deletes it." Rejected.
- **Roll our own session backend over Redis.** Conceivable, but the FastAPI-Users database strategy reuses the same Postgres we already trust for everything else; adding Redis to the session path also adds a dependency that can fail. Rejected for M2.5 — revisit if Postgres session lookup becomes the latency bottleneck (it won't).
- **OAuth-only (Google, Microsoft) with no local passwords.** Operationally simpler — no password hashing path. Rejected for two reasons: BRF board members aren't all on Google Workspace, and the operator-managed account flow doesn't fit OAuth ergonomically. OAuth login is a future addition on top of the password flow, not a replacement.

### Cross-tenant admin alternatives

Five patterns were surveyed against the practitioner literature (Postgres official docs, Supabase, PostgREST, Bytebase, Crunchy Data, AWS RDS guidance). The investigation is logged in this PR's research artefact and cited below.

- **`SECURITY DEFINER` functions** — every admin operation is a SQL function that runs as the schema owner. Smallest attack surface; auditable through `GRANT EXECUTE`. Rejected for our scale: SQLAlchemy ORM-natural code becomes RPC-style; every new admin op needs a migration; paginated lists with dynamic filters are awkward. Excellent choice when the admin surface is small and frozen — likely not ours.
- **Bypass GUC in policies** (`SET LOCAL app.bypass_rls = 'true'`, every policy adds `OR coalesce(current_setting('app.bypass_rls', true)::boolean, false)`). Cheap. Documented (Fritzsche, dev.to write-ups). Rejected: the bypass lives in policy text and application discipline rather than in a Postgres role attribute. Any SQL-injection-equivalent bug in the `unstash_app` code path can emit the `SET LOCAL` and bypass RLS. Against our defense-in-depth posture from ADR 0005.
- **`SET ROLE` per transaction.** PostgREST's dispatch model. Rejected: the Postgres docs themselves warn that `SET ROLE` is unsuitable as a multi-tenancy primitive because `RESET ROLE` reverts it; PgBouncer makes this worse by pooling connections across requests.
- **Restrictive admin policies, no `BYPASSRLS` attribute** — a role exists, has no privilege bypass, but each policy adds `OR session_user = 'unstash_admin'` (or similar). Marginally more auditable than `BYPASSRLS` because the trust is in policy text and grants rather than a role attribute. Rejected on grounds of additional policy maintenance for negligible practical security difference.

The **separate `BYPASSRLS` role** pattern we chose is what Supabase ships in production for its `service_role`, what the Crunchy Data multi-tenant guide recommends for "any operation that crosses tenants," and what aligns most naturally with our existing two-role architecture (`unstash_app` / `unstash_migrations`). The trade-off the literature is consistent about: the admin credential is a high-blast-radius credential, and protecting it is part of the design.

## Consequences

### Positive

- Authentication is a well-understood, library-supported path. No homegrown session machinery.
- Logout actually invalidates. Compromised cookies can be killed immediately.
- Cross-tenant admin routes use natural ORM code — no per-operation migrations or RPC wrappers.
- The role separation is enforced by Postgres, not by application discipline. If an admin route ever forgets to use `get_admin_session`, it gets RLS-blocked rather than silently writing to the wrong tenant.
- The pattern extends cleanly: new admin operations are normal SQLAlchemy code on the admin engine, gated by `current_superuser`.

### Negative

- A third credential to manage. Same file-based secret mechanism (ADR 0003), so the marginal operational cost is small. A rotation runbook for `database_admin_password` will be needed.
- The admin engine is a privileged surface. Any route that mistakenly accepts user-controlled input and passes it through to the admin engine has a much larger blast radius than the same mistake on the app engine. Mitigations: every admin route is explicitly gated by `current_superuser`; admin routes live in their own module (`unstash.admin`) so reviewers know what they're looking at; no admin route accepts a free-form filter or query.
- A future "stop running tests as superuser" exercise — testing routes that legitimately need superuser context means seeding a superuser in test fixtures, which we now do.

### Neutral

- The session strategy is database-backed; latency adds one short PK lookup per authenticated request. Acceptable; faster than the Argon2id verification on login itself.
- The admin engine adds one more connection pool. For our 5-pool default and modest expected admin traffic, irrelevant.

## Operational notes

### Production upgrade path

The `unstash_admin` role does not yet exist on production or staging databases that were provisioned before this ADR. `init-db.sh` runs only on first boot, so updating it is sufficient for fresh databases but not for existing ones. On existing databases:

1. The operator runs `CREATE ROLE unstash_admin LOGIN PASSWORD '<value>' BYPASSRLS NOCREATEDB NOCREATEROLE` once via `psql` as the database superuser.
2. The operator writes the corresponding secret file at `$SECRETS_DIR/database_admin_password` on the deployment host.
3. The deploy then runs migration 0008, which `GRANT`s DML on all existing tables to the new role and re-issues the `ALTER DEFAULT PRIVILEGES` for future tables. The migration is safe to re-run.

For fresh databases (local dev, CI), `init-db.sh` handles everything and the migration is a no-op-equivalent.

### Rotation

`database_admin_password` follows the same rotation cadence as the other database role credentials: a runbook at `docs/runbooks/rotate-database-admin-password.md` (added when needed) describes the steps. The rotation does not require downtime — change the role password, then restart the API container so its admin engine picks up the new secret.

## References

- ADR 0003 — File-based secrets on the VPS
- ADR 0005 — Multi-tenant isolation via application filter + RLS
- PostgreSQL docs: [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [`SET ROLE`](https://www.postgresql.org/docs/current/sql-set-role.html)
- PostgreSQL wiki: [Row Security Considerations](https://wiki.postgresql.org/wiki/Row_Security_Considerations)
- Supabase: [Roles](https://supabase.com/docs/guides/database/postgres/roles), [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- PostgREST: [Authentication](https://docs.postgrest.org/en/v12/references/auth.html)
- Bytebase: [Postgres RLS Footguns](https://www.bytebase.com/blog/postgres-row-level-security-footguns/)
- Crunchy Data: [Row Level Security for Tenants in Postgres](https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres)
- AWS Database Blog: [Multi-tenant data isolation with PostgreSQL Row Level Security](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/)
- Rico Fritzsche: [Mastering PostgreSQL Row Level Security for Multi-Tenancy](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/)
- FastAPI-Users: [Cookie auth backend](https://fastapi-users.github.io/fastapi-users/latest/configuration/authentication/cookie/), [Database strategy](https://fastapi-users.github.io/fastapi-users/latest/configuration/authentication/strategies/database/)
- OWASP Password Storage Cheat Sheet (Argon2id recommendation)
