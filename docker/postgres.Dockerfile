# Custom PostgreSQL image for Unstash.
#
# Base: pgvector/pgvector:pg17 (PostgreSQL 17 with pgvector preinstalled).
# Adds: pgvectorscale (Timescale DiskANN index) and pg_search (ParadeDB BM25).
# Bakes in the database initialization script that creates the Unstash roles.
#
# All versions are pinned for reproducibility — bump deliberately.

FROM pgvector/pgvector:pg17

ARG PG_MAJOR=17
ARG PGVECTORSCALE_VERSION=0.9.0
ARG PG_SEARCH_VERSION=0.23.1
ARG DEBIAN_CODENAME=bookworm

# ---------------------------------------------------------------------------
# Install pgvectorscale and pg_search from prebuilt .deb packages.
# ---------------------------------------------------------------------------
# pgvectorscale ships as a .deb inside a .zip on its GitHub release page.
# pg_search ships as a .deb directly on the ParadeDB GitHub release page.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        unzip \
        libicu-dev; \
    ARCH=$(dpkg --print-architecture); \
    # pgvectorscale
    curl -fsSL -o /tmp/pgvectorscale.zip \
        "https://github.com/timescale/pgvectorscale/releases/download/${PGVECTORSCALE_VERSION}/pgvectorscale-${PGVECTORSCALE_VERSION}-pg${PG_MAJOR}-${ARCH}.zip"; \
    unzip -d /tmp/pgvectorscale /tmp/pgvectorscale.zip; \
    apt-get install -y --no-install-recommends \
        "/tmp/pgvectorscale/pgvectorscale-postgresql-${PG_MAJOR}_${PGVECTORSCALE_VERSION}-Linux_${ARCH}.deb"; \
    # pg_search
    curl -fsSL -o /tmp/pg_search.deb \
        "https://github.com/paradedb/paradedb/releases/download/v${PG_SEARCH_VERSION}/postgresql-${PG_MAJOR}-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-${DEBIAN_CODENAME}_${ARCH}.deb"; \
    apt-get install -y --no-install-recommends /tmp/pg_search.deb; \
    # Cleanup
    rm -rf /tmp/pgvectorscale.zip /tmp/pgvectorscale /tmp/pg_search.deb; \
    apt-get purge -y --auto-remove curl unzip; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Initialization script — creates the Unstash database roles on first boot.
# ---------------------------------------------------------------------------
COPY docker/init-db.sh /docker-entrypoint-initdb.d/01-init-app-user.sh
RUN chmod +x /docker-entrypoint-initdb.d/01-init-app-user.sh
