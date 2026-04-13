# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage: builder — install dependencies and build
# ---------------------------------------------------------------------------
FROM node:22-slim AS builder

RUN corepack enable pnpm

WORKDIR /app

# Install dependencies first (layer caching).
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy source and build.
COPY frontend/ ./
RUN pnpm run build

# Prune to production dependencies only.
RUN pnpm install --frozen-lockfile --prod

# ---------------------------------------------------------------------------
# Stage: dev — local development with hot reload
# ---------------------------------------------------------------------------
FROM builder AS dev

# Reinstall all dependencies (including devDependencies) for dev.
RUN pnpm install --frozen-lockfile

EXPOSE 5173
CMD ["pnpm", "run", "dev", "--host", "0.0.0.0"]

# ---------------------------------------------------------------------------
# Stage: runtime — minimal production image
# ---------------------------------------------------------------------------
FROM node:22-slim AS runtime

RUN groupadd --system unstash && \
    useradd --system --gid unstash --home-dir /app --no-create-home unstash

WORKDIR /app

COPY --from=builder /app/build ./build
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules

ENV NODE_ENV=production

USER unstash

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "const h=require('http');h.get('http://localhost:3000',(r)=>{process.exit(r.statusCode===200?0:1)}).on('error',()=>process.exit(1))"]

CMD ["node", "build"]
