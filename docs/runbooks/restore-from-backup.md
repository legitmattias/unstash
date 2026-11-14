# Restore from backup

## Purpose

Restore one environment's database from a logical dump, and its uploaded documents from a copy of the documents directory. Covers both recovery on the same host and carrying data over to a new one.

## When to use it

- Data was lost or corrupted and the last good state is a nightly dump.
- An environment is moving to a new host.
- A restore drill: proving that the dumps being taken can actually be restored.

This restores to the moment the dump was taken. There is no point-in-time recovery; anything written after the dump is lost.

## Prerequisites

- Root or deploy access on the target host.
- A dump produced by `scripts/backup-database.sh` (custom format), or by an equivalent `pg_dump --format=custom` as the migrations role.
- The environment already deployed on the target host, so the database, its roles and its extensions exist. A restore does not create roles or install extensions.
- The schema revision of the dump must not be newer than the deployed code. Check with `pg_restore --list <dump> | grep alembic_version` and compare against the deployed revision if in doubt.

## Procedure

`<project>` is the compose project name for the environment.

### 1. Check the dump before touching anything

```bash
docker exec -i <project>-postgres-1 pg_restore --list < $DUMP | head
```

A readable table of contents means the archive is intact. If this fails, stop: the dump is unusable and the current database is still the best copy.

### 2. Take a dump of the current state

Even a damaged database is evidence, and this makes the restore itself reversible:

```bash
systemctl start unstash-backup@<environment>.service
ls -l $BACKUP_ROOT/<environment>/
```

Skip only when the target database is known to be empty.

### 3. Stop the application

```bash
docker stop <project>-api-1 <project>-worker-1
```

Leave postgres running. Stopping the writers prevents rows arriving mid-restore and releases the locks the restore needs.

### 4. Restore

```bash
docker exec -i <project>-postgres-1 pg_restore \
    -U unstash_migrations -d unstash \
    --clean --if-exists --no-owner --role=unstash_migrations \
    < $DUMP
```

**Expect a small number of errors, reported as "errors ignored on restore".** They come from objects the migrations role does not own: the extensions themselves, and the search extension's internal schema and cache table. Those already exist on the target, installed at first boot, so failing to drop and recreate them is harmless. Every application table, index, policy and grant belongs to the migrations role and restores normally.

An error naming an application table (anything in the `public` schema) is not expected. Stop and investigate.

### 5. Restore the documents

Copy the saved documents directory into `$DATA_ROOT/<environment>/documents`, then restore ownership:

```bash
chown -R unstash:unstash $DATA_ROOT/<environment>/documents
```

The database refers to documents by path under this directory, so the two must come from the same point in time. A database restored without its documents still searches — chunk text lives in the database — but the original files cannot be opened.

### 6. Start the application

```bash
docker start <project>-api-1 <project>-worker-1
```

The api refuses to start if the restored schema is not at the code's migration head. If that happens, the dump is from an older revision: apply the pending migrations (see `manual-database-migration.md`) and start it again.

## Verification

All of these, not just the first:

- `curl -sf http://127.0.0.1:<api-port>/api/ready` returns 200.
- Row counts match the source, where the source is still available:

  ```bash
  docker exec <project>-postgres-1 psql -U postgres -d unstash -Atc \
    "select (select count(*) from documents), (select count(*) from chunks),
            (select count(*) from users), (select count(*) from organisations)"
  ```

- Row-level security survived: `select count(*) from pg_class where relrowsecurity` and `select count(*) from pg_policies` match the source. A restore that silently lost its policies would still serve requests.
- Every table in `public` is owned by `unstash_migrations` (`select tableowner, count(*) from pg_tables where schemaname = 'public' group by 1`).
- Both search indexes exist: `ix_chunks_embedding_diskann` and `ix_chunks_text_bm25`.
- A user can log in and a search returns results.

## Rollback

Restore the dump taken in step 2, by the same procedure.

## Escalation

If the restore reports errors on application tables, or the api will not start after migrations are applied, the dump and the code disagree about the schema. Do not force it. Restore the step 2 dump to return to the starting state, then compare the dump's schema revision with the deployed revision.

If no usable dump exists, the remaining copy is the host's disk-level backup, which restores the whole host to the time of the image.
