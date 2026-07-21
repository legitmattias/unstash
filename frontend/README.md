# Frontend

Next.js frontend for Unstash.

## Stack

- Next.js (App Router) with React 19 and TypeScript
- React Server Components for data fetching; a thin BFF forwards the session
  cookie to the FastAPI backend (FastAPI stays the auth source of truth)
- Open Props design tokens + CSS Modules for component styles
- Radix UI primitives for accessible interactive components
- `next/font` (self-hosted) for typography
- Standalone output for the Docker runtime image
- pnpm for package management; Node 24

## Development

```sh
pnpm install
pnpm dev
```

The dev server proxies `/api/*` to the backend (default `http://localhost:8000`,
override with `API_INTERNAL_ORIGIN`). In production Caddy routes `/api/*` to the
backend directly.

## Scripts

- `pnpm dev` — start the dev server
- `pnpm build` — production build (runs the TypeScript type-check)
- `pnpm start` — serve the production build
- `pnpm check` — TypeScript check (`tsc --noEmit`; requires a prior build for
  Next-generated types)

## Structure

- `app/` — routes (App Router), colocated client components and CSS Modules
- `app/lib/` — server helpers: API/BFF, session, i18n, document-type mapping
- `app/components/` — shared chrome (header, language switcher, theme toggle)
- `app/globals.css` — design tokens (Direction A) and base styles
