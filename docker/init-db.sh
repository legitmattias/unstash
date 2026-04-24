#!/bin/bash
set -euo pipefail

# Create the two Unstash database roles on first boot:
#   - unstash_migrations: owns the schema, BYPASSRLS, used only by Alembic
#   - unstash_app:        DML only, NOBYPASSRLS, used by application code at runtime
#
# Passwords are read from either:
#   - Environment variables: UNSTASH_APP_DB_PASSWORD, UNSTASH_MIGRATIONS_DB_PASSWORD
#   - Docker Compose secrets: /run/secrets/database_password
#                             /run/secrets/database_migrations_password

read_password() {
    local env_var="$1"
    local secret_file="$2"
    local role_name="$3"
    local env_value="${!env_var:-}"

    if [ -n "$env_value" ]; then
        printf '%s' "$env_value"
    elif [ -f "$secret_file" ]; then
        cat "$secret_file"
    else
        echo "ERROR: No password for role '${role_name}' available." >&2
        echo "Set ${env_var} or mount ${secret_file}." >&2
        exit 1
    fi
}

APP_PASSWORD=$(read_password UNSTASH_APP_DB_PASSWORD /run/secrets/database_password unstash_app)
MIGRATIONS_PASSWORD=$(read_password UNSTASH_MIGRATIONS_DB_PASSWORD /run/secrets/database_migrations_password unstash_migrations)

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Install required extensions (requires superuser). Migrations can then
    -- assume they exist and simply reference them with CREATE EXTENSION IF NOT
    -- EXISTS — which is idempotent and documents the dependency.
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
    CREATE EXTENSION IF NOT EXISTS pg_search;

    -- Migrations role: owns the schema, bypasses RLS, used only by Alembic.
    CREATE ROLE unstash_migrations
        LOGIN
        PASSWORD '${MIGRATIONS_PASSWORD}'
        NOSUPERUSER
        BYPASSRLS
        NOCREATEDB
        NOCREATEROLE;

    -- Application role: DML only, cannot bypass RLS, cannot alter schema.
    CREATE ROLE unstash_app
        LOGIN
        PASSWORD '${APP_PASSWORD}'
        NOSUPERUSER
        NOBYPASSRLS
        NOCREATEDB
        NOCREATEROLE;

    -- Give the migrations role ownership of the public schema so it can freely
    -- CREATE / ALTER / DROP objects. The application role cannot alter the schema.
    ALTER SCHEMA public OWNER TO unstash_migrations;

    -- Application role can connect to the database and USE the schema but not
    -- modify it.
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO unstash_app;
    GRANT USAGE ON SCHEMA public TO unstash_app;

    -- When the migrations role creates tables or sequences, the application role
    -- automatically gets the appropriate DML privileges. RLS policies then
    -- restrict which rows the application can read or write.
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO unstash_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO unstash_app;
EOSQL

echo "Roles 'unstash_migrations' and 'unstash_app' created."
