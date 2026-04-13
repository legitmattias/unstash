#!/bin/bash
set -euo pipefail

# Create the restricted application database user.
# The migrations user (POSTGRES_USER) is the superuser that owns the schema.
# The app user (unstash_app) has only DML privileges — no DDL, no superuser.
# This separation is required for Row-Level Security to be effective.

APP_PASSWORD="$(< /run/secrets/database_password)"

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
