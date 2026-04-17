# Custom PostgreSQL image for Unstash — stock postgres:17 with the Unstash
# database initialization script baked in. The init script creates the
# restricted `unstash_app` user with DML-only privileges (no DDL, no superuser)
# on first container start.
#
# Extensions (pgvector, pgvectorscale, ParadeDB pg_search) will be added to
# this image in a later milestone when schema work begins.

FROM postgres:17

COPY docker/init-db.sh /docker-entrypoint-initdb.d/01-init-app-user.sh

RUN chmod +x /docker-entrypoint-initdb.d/01-init-app-user.sh
