#!/bin/bash
#
# Entrypoint wrapper for the Unstash PostgreSQL image.
#
# The stock entrypoint runs first-boot init scripts as the `postgres` user.
# Role passwords arrive as file secrets whose host ownership suits the
# application containers, so `postgres` cannot be assumed able to read them.
# While still root, this copies them to a memory-backed directory owned by
# `postgres`, where the init script reads them, then hands over to the stock
# entrypoint unchanged.

set -euo pipefail

SECRETS_SOURCE="/run/secrets"
SECRETS_FOR_INIT="/dev/shm/unstash-db-secrets"

if [ "$(id -u)" = "0" ]; then
    install -d -m 0700 -o postgres -g postgres "$SECRETS_FOR_INIT"
    for name in database_password database_migrations_password database_admin_password; do
        if [ -f "${SECRETS_SOURCE}/${name}" ]; then
            install -m 0400 -o postgres -g postgres \
                "${SECRETS_SOURCE}/${name}" "${SECRETS_FOR_INIT}/${name}"
        fi
    done
fi

exec docker-entrypoint.sh "$@"
