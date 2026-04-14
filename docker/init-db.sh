#!/bin/bash
set -euo pipefail

# Create the restricted application database user.
# Reads the password from either:
#   - UNSTASH_APP_DB_PASSWORD env var (production, set by CI)
#   - /run/secrets/database_password file (local dev, Docker Compose secrets)

if [ -n "${UNSTASH_APP_DB_PASSWORD:-}" ]; then
    APP_PASSWORD="$UNSTASH_APP_DB_PASSWORD"
elif [ -f /run/secrets/database_password ]; then
    APP_PASSWORD="$(< /run/secrets/database_password)"
else
    echo "ERROR: No app database password available." >&2
    echo "Set UNSTASH_APP_DB_PASSWORD or mount /run/secrets/database_password" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE unstash_app
        LOGIN
        PASSWORD '${APP_PASSWORD}'
        NOSUPERUSER
        NOCREATEDB
        NOCREATEROLE;

    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO unstash_app;
    GRANT USAGE ON SCHEMA public TO unstash_app;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO unstash_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO unstash_app;
EOSQL

echo "Application user 'unstash_app' created."
