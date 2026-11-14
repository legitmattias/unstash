#!/usr/bin/env bash
#
# Dump one environment's database to a dated file and prune old dumps.
#
# Runs pg_dump inside the environment's postgres container and writes a
# custom-format archive on the host. The dump is a consistent logical
# snapshot, so a disk-level backup of the host that includes this directory
# always contains a restorable copy of the database, whatever state the live
# data directory was in when the disk was imaged.
#
# Exits non-zero, leaving no partial file, if the dump fails or is empty.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: backup-database.sh <environment> [--help]

  <environment>   Compose project suffix, e.g. "staging" or "production".
                  The container dumped is unstash-<environment>-postgres-1.

Environment:
  BACKUP_ROOT      Parent of <environment>/ dump directories. Default: /var/backups/unstash
  RETENTION_DAYS   Dumps older than this are deleted.          Default: 14
EOF
}

if [[ "${1:-}" == "--help" || $# -ne 1 ]]; then
    usage
    [[ "${1:-}" == "--help" ]] && exit 0
    exit 2
fi

environment="$1"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/unstash}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

container="unstash-${environment}-postgres-1"
target_dir="${BACKUP_ROOT}/${environment}"
target="${target_dir}/unstash-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="${target}.partial"

if ! docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | grep -qx true; then
    echo "error: container ${container} is not running" >&2
    exit 1
fi

install -d -m 0700 "$target_dir"
trap 'rm -f "$partial"' EXIT

echo "dumping ${container} to ${target}"
docker exec "$container" pg_dump -U unstash_migrations -d unstash --format=custom >"$partial"

if [[ ! -s "$partial" ]]; then
    echo "error: dump is empty" >&2
    exit 1
fi

# Reads the archive's table of contents; fails on a truncated or corrupt dump.
docker exec -i "$container" pg_restore --list <"$partial" >/dev/null

chmod 0600 "$partial"
mv "$partial" "$target"
echo "wrote $(du -h "$target" | cut -f1) ${target}"

find "$target_dir" -name 'unstash-*.dump' -type f -mtime "+${RETENTION_DAYS}" -print -delete
