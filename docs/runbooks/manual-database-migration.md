# Manual database migration

## Purpose

Apply pending Alembic migrations to a deployed database. This is the manual procedure used until the deploy workflow runs `alembic upgrade head` automatically (planned).

## When to use it

- A release has shipped that includes new Alembic migrations.
- The deploy workflow has completed (CI green, new container images deployed).
- The application schema is behind the application code. Symptoms include: routes failing because expected tables don't exist, or `alembic current` showing a revision earlier than `alembic heads`.

## Prerequisites

- Shell access on the deployment host.
- Membership in the `docker` group on the host (or equivalent — running `docker exec` without sudo).
- The host's secret directory in place at the path the compose file's `${SECRETS_DIR}` variable points to.
- A recent database backup (this runbook includes the step to take one).

## Procedure

The application database is upgraded by running Alembic inside the live api container. The runtime image contains `alembic.ini`, the migration scripts, and the dependencies; the container has the migrations role's password mounted at `/run/secrets/database_migrations_password`.

### 1. Confirm the container has the migration prerequisites

```bash
docker exec <project>-api-1 ls /run/secrets/
```

Expect at minimum: `database_password`, `database_migrations_password`, `session_secret`, `encryption_key`. If `database_migrations_password` is missing, the compose file is misconfigured — fix the compose file and redeploy before continuing.

```bash
docker exec <project>-api-1 alembic --version
```

Should print the installed Alembic version. If the binary isn't found, the runtime image is missing the migration tooling — fix the Dockerfile and rebuild before continuing.

### 2. Take a database backup

Backups for manual migrations live under a per-environment directory in the operator's home so they don't accumulate at the top level. Create the directory the first time and then use it consistently:

```bash
BACKUP_DIR=~/backups/unstash/<environment>
mkdir -p "$BACKUP_DIR"

docker exec <project>-postgres-1 pg_dump \
    -U unstash_migrations -d unstash \
    --format=custom --file=/tmp/pre-migration-backup.dump

docker cp <project>-postgres-1:/tmp/pre-migration-backup.dump \
    "$BACKUP_DIR/pre-migration-$(date +%F-%H%M).dump"

ls -lh "$BACKUP_DIR/pre-migration-"*.dump
```

`<environment>` is `staging` or `production` — match it to the project name the deploy workflow uses (`unstash-staging` or `unstash-production`). Verify the file exists and the size is non-trivial. If the backup fails, **stop here** — do not migrate without a backup.

### 3. Retro-fit the migrations role's CREATE-on-database grant (one-off per database)

If the database was initialised by an `init-db.sh` that did not include `GRANT CREATE ON DATABASE unstash TO unstash_migrations`, run the grant manually. Without it, `CREATE EXTENSION IF NOT EXISTS` in migrations fails on databases where the extension does not already exist.

Idempotent; safe to run on databases that already have the grant:

```bash
docker exec <project>-postgres-1 psql -U postgres -d unstash -c \
    "GRANT CREATE ON DATABASE unstash TO unstash_migrations;"
```

Future databases initialised from the current `init-db.sh` will already have this grant.

If a migration adds a new extension and the migration role still lacks `CREATE` on the database, the migration fails with `permission denied to create extension "<name>"`. Pre-create the extension as superuser:

```bash
docker exec -it <project>-postgres-1 psql -U postgres -d unstash \
    -c "CREATE EXTENSION IF NOT EXISTS <extension_name>;"
```

### 4. Dry-run the upgrade

Alembic's `--sql` mode generates *static* SQL — it does not consult the database for the current revision, it emits the full chain starting from base. That's noisy when only the last one or two migrations need to apply.

To inspect just the SQL that would actually run, supply an explicit range `<from>:head`, where `<from>` is the current revision shown in step 5 below (or `base` for an empty database):

```bash
docker exec <project>-api-1 alembic upgrade <current-revision>:head --sql | less
```

If you don't yet know the current revision, the static form is still useful — just scroll to the bottom for the incremental tail:

```bash
docker exec <project>-api-1 alembic upgrade head --sql | less
```

Either way, expect `CREATE TABLE`, `CREATE INDEX`, `ALTER TABLE`, `INSERT INTO alembic_version` for each pending migration. If anything looks unexpected — a `DROP TABLE` you did not author, an `ALTER` that suggests data loss — stop and investigate before applying.

### 5. Check the current revision

```bash
docker exec <project>-api-1 alembic current
```

Note the revision (or empty result, meaning base). This is the target if you need to downgrade.

### 6. Apply the upgrade

```bash
docker exec <project>-api-1 alembic upgrade head
```

Each migration runs in its own transaction. A failure mid-way rolls back that specific migration cleanly; previously-applied migrations remain applied.

### 7. Verify

```bash
docker exec <project>-api-1 alembic current
```

Should show the new head revision with `(head)`.

```bash
docker exec <project>-postgres-1 psql -U unstash_migrations -d unstash -c '\dt'
```

Should list all expected tables, owned by `unstash_migrations`.

If application code reads from any of the new tables, do a quick smoke query as the application role:

```bash
docker exec <project>-postgres-1 psql -U unstash_app -d unstash -c \
    "SELECT count(*) FROM <one of the new tables>;"
```

Should return 0 without permission errors. A `permission denied` here indicates the `ALTER DEFAULT PRIVILEGES` chain in `init-db.sh` did not fire for these tables — see Recovery below.

## Verification

The procedure is verified when all four hold:

- `alembic current` shows the expected new head revision.
- Every expected table exists in `\dt` output, owned by `unstash_migrations`.
- The application role can run a `SELECT count(*)` against the new tables without errors.
- The application is still serving requests on its health endpoint.

## Rollback

If a migration fails partway through and you need to undo what did apply:

```bash
docker exec <project>-api-1 alembic downgrade <previous-revision>
```

If the migration chain is corrupt (mismatched expectations), restore the backup from step 2:

```bash
docker cp ~/backups/unstash/<environment>/pre-migration-<timestamp>.dump \
    <project>-postgres-1:/tmp/restore.dump

docker exec <project>-postgres-1 pg_restore \
    -U unstash_migrations -d unstash --clean --if-exists \
    /tmp/restore.dump
```

Restoration requires the database to have no active connections that lock the affected tables. Stop the api container first if needed.

## Recovery from missing default privileges

If a smoke query as the application role fails with `permission denied` on a freshly migrated table, the `ALTER DEFAULT PRIVILEGES` in `init-db.sh` did not register for the migrations role's table-creation events. Grant directly:

```bash
docker exec <project>-postgres-1 psql -U postgres -d unstash -c \
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO unstash_app;
     GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO unstash_app;"
```

This grants on existing objects. For future migrations, ensure the `ALTER DEFAULT PRIVILEGES` lines in `init-db.sh` are present in the version that initialised the database.

## Escalation

If the migration fails and rollback also fails, the database is in an unknown state. Restore from the backup taken in step 2. If the backup itself is corrupt or missing, the next backup tier is the scheduled backup (when implemented) or the cloud provider's volume snapshot.

If `psql` as the superuser also fails — the database is unreachable from the host — the issue is below the database layer. Check container logs (`docker logs <project>-postgres-1`), the data volume's filesystem health, and the host's resource availability.
