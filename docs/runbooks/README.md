# Runbooks

Operational procedures for recurring tasks and common incidents.

Runbooks are written during development when the relevant code is fresh, and updated after incidents when we learn what actually breaks. The goal: when something goes wrong, there's a procedure to follow instead of debugging from scratch.

## Index

Written:

- `vps-maintenance.md` — shared VPS operations: tier upgrades, volume management, OS patching, reboots, common stuck states.

Planned (added as the relevant components are implemented):

- `deploy.md` — deployment procedure
- `rollback.md` — rolling back a deployment
- `restore-from-backup.md` — PostgreSQL point-in-time restore
- `rotate-secrets.md` — secret rotation procedures
- `investigate-failed-sync.md` — debugging connector sync failures
- `search-quality-debugging.md` — debugging poor search results
- `incident-response.md` — general incident handling
- `data-breach-response.md` — GDPR 72-hour notification procedure

## Format

Each runbook follows a consistent structure:

1. **Purpose** — what this runbook is for
2. **When to use it** — symptoms or triggers
3. **Prerequisites** — access, credentials, tools needed
4. **Procedure** — numbered steps, each verifiable
5. **Verification** — how to confirm success
6. **Rollback** — how to undo if the procedure goes wrong
7. **Escalation** — where to turn if the runbook doesn't resolve the issue

Runbooks must be tested. If the steps can't be dry-run or practiced in staging, they aren't trustworthy.
