# ---------------------------------------------------------------------------
# Stage: deps — install dependencies (cached on lockfile)
# ---------------------------------------------------------------------------
FROM node:24-slim AS deps

RUN corepack enable pnpm
WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# ---------------------------------------------------------------------------
# Stage: builder — produce the standalone server
# ---------------------------------------------------------------------------
FROM node:24-slim AS builder

RUN corepack enable pnpm
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN pnpm build

# ---------------------------------------------------------------------------
# Stage: dev — local development with hot reload
# ---------------------------------------------------------------------------
FROM node:24-slim AS dev

RUN corepack enable pnpm
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

EXPOSE 3000
CMD ["pnpm", "dev", "--hostname", "0.0.0.0"]

# ---------------------------------------------------------------------------
# Stage: runtime — minimal standalone server
# ---------------------------------------------------------------------------
FROM node:24-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN groupadd --system --gid 1001 nodejs && \
    useradd --system --uid 1001 --gid nodejs nextjs

# The standalone output carries its own trimmed node_modules and server.js;
# static assets and the public dir are copied alongside it.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://localhost:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
