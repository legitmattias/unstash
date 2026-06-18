#!/bin/bash
set -euo pipefail

# Create the three Unstash database roles on first boot:
#   - unstash_migrations: owns the schema, BYPASSRLS, used only by Alembic
#   - unstash_app:        DML only, NOBYPASSRLS, used by application code at runtime
#   - unstash_admin:      DML only, BYPASSRLS, used only by superuser-gated
#                         cross-tenant admin endpoints (no DDL — only Alembic
#                         owns the schema)
#
# Passwords are read from either:
#   - Environment variables: UNSTASH_APP_DB_PASSWORD, UNSTASH_MIGRATIONS_DB_PASSWORD,
#                            UNSTASH_ADMIN_DB_PASSWORD
#   - Docker Compose secrets: /run/secrets/database_password
#                             /run/secrets/database_migrations_password
#                             /run/secrets/database_admin_password

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
ADMIN_PASSWORD=$(read_password UNSTASH_ADMIN_DB_PASSWORD /run/secrets/database_admin_password unstash_admin)

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Install required extensions (requires superuser). Migrations can then
    -- assume they exist and simply reference them with CREATE EXTENSION IF NOT
    -- EXISTS — which is idempotent and documents the dependency.
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
    CREATE EXTENSION IF NOT EXISTS pg_search;
    CREATE EXTENSION IF NOT EXISTS citext;

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

    -- Admin role: DML only with BYPASSRLS, used solely by superuser-gated
    -- HTTP routes that legitimately operate across tenants (user CRUD,
    -- cross-org membership management, etc.). Pattern A from the M2.5-A
    -- ADR — see docs/adr/0006-auth-and-cross-tenant-admin.md.
    --
    -- Cannot alter the schema. Cannot create roles. The BYPASSRLS attribute
    -- is the only difference from unstash_app and is what makes cross-tenant
    -- reads/writes possible without poking a hole in the per-tenant policies.
    CREATE ROLE unstash_admin
        LOGIN
        PASSWORD '${ADMIN_PASSWORD}'
        NOSUPERUSER
        BYPASSRLS
        NOCREATEDB
        NOCREATEROLE;

    -- Give the migrations role ownership of the public schema so it can freely
    -- CREATE / ALTER / DROP objects. The application role cannot alter the schema.
    ALTER SCHEMA public OWNER TO unstash_migrations;

    -- Allow the migrations role to install trusted extensions itself.
    -- CREATE EXTENSION IF NOT EXISTS still needs CREATE on the database even
    -- when the extension already exists — the existence check is gated by
    -- privilege.
    GRANT CREATE ON DATABASE ${POSTGRES_DB} TO unstash_migrations;

    -- Application and admin roles can connect to the database and USE the
    -- schema but not modify it.
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO unstash_app;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO unstash_admin;
    GRANT USAGE ON SCHEMA public TO unstash_app;
    GRANT USAGE ON SCHEMA public TO unstash_admin;

    -- When the migrations role creates tables or sequences, the application
    -- and admin roles automatically get the appropriate DML privileges. RLS
    -- policies restrict unstash_app to its own org; unstash_admin's BYPASSRLS
    -- makes the policies inert for cross-tenant admin operations.
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO unstash_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO unstash_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO unstash_admin;
    ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO unstash_admin;
EOSQL

echo "Roles 'unstash_migrations', 'unstash_app', and 'unstash_admin' created."
