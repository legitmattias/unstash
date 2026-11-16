# Import a folder

## Purpose

Load an existing folder of documents into one organisation, keeping each file's place in the folder structure and its modification time.

## When to use it

- An organisation is onboarded with an archive that already exists on disk.
- Documents were ingested before their folder paths were recorded, and the paths should be added.

The command is a client of the upload API. It does nothing a signed-in member could not do one file at a time, and it runs from any machine that holds the files and can reach the service.

## Prerequisites

- The backend package installed where the files are (`uv sync` in `backend/`).
- An API token scoped to the organisation, minted by a superuser through `POST /api/admin/users/{user_id}/tokens` with an expiry. Store it in a file readable only by you; the command takes the file's path, never the token itself.
- Enough headroom under the organisation's daily upload limit, if one is set. Duplicates do not count against it.

## Procedure

`$ROOT` is the folder to import, `$URL` the service's base URL, `$ORG` the organisation slug.

1. List what would be sent. Nothing is contacted and the token is not read.

   ```sh
   uv run python -m unstash.importer "$ROOT" --url "$URL" --org "$ORG" \
       --token-file "$TOKEN_FILE" --dry-run
   ```

   Check the count and total size, and that the listed paths start where you expect. Use `--prefix` to place every path under a common parent.

2. Import a handful first.

   ```sh
   uv run python -m unstash.importer "$ROOT" --url "$URL" --org "$ORG" \
       --token-file "$TOKEN_FILE" --limit 10
   ```

   Wait for those documents to reach `indexed` before continuing; see [document-ingestion.md](document-ingestion.md) for where to look.

3. Import everything by repeating the command without `--limit`. Files sent in step 2 come back as `duplicate`.

Each line of output is one file and its outcome:

| Outcome | Meaning |
|---|---|
| `created` | Stored and queued for ingestion. |
| `duplicate` | The same content already exists; this path was added to that document. |
| `too_large` | Over the upload size limit. Not stored. |
| `rejected` | The service refused the request (authentication, daily limit, invalid path). Not retried. |
| `failed` | No usable answer after several attempts. |

Skipped without a line: empty files, symbolic links, hidden directories, and operating-system or office lock files.

The exit status is non-zero if any file was `rejected` or `failed`.

## Verification

- The final summary line accounts for every file listed by the dry run.
- A document's locations are returned by `GET /api/orgs/{slug}/documents/{id}`; spot-check a file known to exist in two folders and confirm both paths are present.
- Uploading only queues ingestion. Follow the queue to empty as described in [document-ingestion.md](document-ingestion.md).

## Rollback

Repeating the command is safe and is the recovery for an interrupted run. There is no bulk undo, so run the dry run and the small batch first.

## Escalation

Every file `rejected`: check the token's expiry and organisation scope, then the daily upload limit. Repeated `failed`: check service health and the API logs before retrying.
