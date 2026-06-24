# ADR 0007: Frontend — Next.js (App Router) over SvelteKit

## Status

Accepted. Supersedes the **Frontend** section of ADR 0001.

## Context

ADR 0001 chose SvelteKit + Open Props + Svelte scoped styles + Bits UI + Iconify + pnpm for the frontend. At the time, the rationale was framework familiarity and a small, ergonomic stack appropriate for a single operator-developer.

Two facts have changed the relevant trade-off:

1. **No frontend code beyond a landing stub exists yet.** M4 is the first milestone that builds substantive UI (search interface and results). M5–M8 follow. The marginal cost of switching the framework now is near zero; the marginal cost of switching after M4–M8 ship is high (rewriting components, re-doing styling, re-doing routing).

2. **The product doubles as a portfolio piece.** Unstash is the operator's flagship public-facing example of production AI engineering plus full-stack delivery. For that role, the frontend stack's ubiquity in the target market matters. The Stockholm / EU hiring market is dominated by React + Next.js: hiring listings routinely specify "React (Next.js)" by name, React appears in ~50–60% of frontend job postings, and SvelteKit demand is roughly an order of magnitude thinner. The operator already has SvelteKit proficiency (Curios, Traiceless); the React + Next.js learning is a deliberate skill investment the project provides a believed-in home for.

The decision affects only the frontend. The backend (FastAPI), database (Postgres + pgvector + pgvectorscale), search infrastructure, document ingestion, ML tooling, deployment topology, and operational posture from ADR 0001 are unchanged.

## Decision

### Framework: Next.js (App Router), React 19, TypeScript

- **Next.js with the App Router** as the framework. Currently v16 stable. React 19 underneath. TypeScript throughout.
- **React Server Components** for data fetching against the FastAPI backend. RSC fetches happen on the Next.js server and stream HTML to the client; the API origin is reachable from the server-side without exposing CORS surface.
- **Server Actions** for mutations where appropriate (form submissions, settings changes). Standard pattern in the App Router.
- **SSR / streaming** as needed for search results pages and other latency-sensitive views.

### Styling and component layer

- **Radix UI primitives** for headless, accessible components (dialogs, dropdowns, comboboxes, popovers, etc.). The React analogue of Bits UI from the prior stack.
- **CSS Modules** for component-level styles. First-class in Next.js, clean in Server Components, no runtime CSS-in-JS overhead, no RSC-boundary friction.
- **Open Props + CSS custom properties** for design tokens. Carries over from the operator's existing SvelteKit work and matches the styling approach used in Dossier (another personal project on Remix). Same tokens, different framework.
- **No Tailwind.** Tailwind's aesthetics are not a fit for the operator's design preference. The CSS layer is also a secondary signal for hiring (employers screen on App Router / RSC / Server Actions much more than on the styling approach). Vanilla Extract and Panda CSS were considered and rejected as unnecessary complexity for this goal.
- **pnpm** stays as the package manager. Framework-agnostic.
- **Iconify** stays — it has first-party React bindings.

### Hosting and deployment

- **Self-hosted on the existing Hetzner + Docker + Caddy infrastructure** (the `mattic-one` VPS). Next.js runs in a container with `output: 'standalone'` mode.
- **Not Vercel.** This is a hard constraint, not a preference: Vercel hosting would compromise the EU-hosting / GDPR posture established in ADR 0001 and the self-hosted operational model established in ADRs 0002 and 0003. Even Vercel's EU regions are insufficient because the customer data path would still traverse Vercel's infrastructure and control plane.

### Interaction with auth (ADR 0006)

The `HttpOnly unstash_session` cookie model from ADR 0006 is preserved. FastAPI remains the **source of truth** for authentication. Next.js acts as a **presentation / Backend-for-Frontend (BFF) layer** in front of the API:

- Server Components, route handlers, and Server Actions in Next.js proxy to FastAPI, forwarding the session cookie.
- Same-site cookie behaviour is straightforward when Next.js and FastAPI are served from the same parent domain via Caddy (which is how the existing topology works).
- Next.js does **not** become a second auth system. There is no NextAuth, no auth state in Next.js, no session secrets in the Next.js container. FastAPI sets the cookie; Next.js forwards it; the route handler in FastAPI validates it.

## Alternatives considered

### Keep SvelteKit (status quo)

The fastest delivery option. The operator is already proficient with SvelteKit and uses it in other personal projects (Curios, Traiceless). Cohesion across the operator's portfolio would be slightly higher with a unified Svelte stack.

Rejected because:
- The portfolio purpose of Unstash dominates here. SvelteKit is the weakest hiring signal of the three serious options for the target market.
- The React + Next.js learning goal is real and ongoing. A throwaway side project as the vehicle would be wasted effort; Unstash is the believed-in home for that learning.
- The switching cost is genuinely near-zero right now (no UI built). Postponing the decision until after M4 makes it expensive.

### Remix / React Router v7 (framework mode)

The closest conceptual on-ramp from SvelteKit — loaders and actions in React Router v7 map almost directly onto SvelteKit's load functions and form actions. The operator's existing Dossier project uses Remix and could share patterns.

Rejected because:
- "Next.js" is the name the market recognises. React Router v7-as-framework and "Remix 3" (Preact fork, no stable release as of mid-2026) are weaker hiring signals.
- The hiring goal is to target the employable default, not the gentlest migration. The harder learning curve is the point.

### TanStack Start (v1.0, early 2026)

Strong type safety, real momentum, growing community.

Rejected because:
- Still small adoption relative to Next.js. Not yet a market-default hiring signal.
- The learning investment in TanStack Start doesn't compound as broadly as Next.js does (TanStack Query, TanStack Router, TanStack Form are all valuable independently but don't sum to the same employability footprint).

### Astro

Excellent for content / marketing sites. Not an interactive app shell — search UI, settings, admin would fight Astro's strengths.

Rejected for the application. Astro may show up later as a separate marketing site (`unstash.com`) deployed separately; that's out of scope for this ADR.

### Vercel hosting

Operationally simplest deployment path for Next.js. Build, push, done.

Rejected: hard constraint. Violates the EU-hosting / GDPR posture (ADR 0001) and the self-hosted operational model (ADR 0002 / 0003). The customer data path must remain on Hetzner.

## Consequences

### Positive

- **Strongest hiring signal for the flagship's portfolio purpose.** Next.js + React + FastAPI + production AI is one of the most widely-requested stack combinations in the target market.
- **Believed-in vehicle for the React / Next.js learning goal.** App Router, RSC, Server Actions, the React 19 hooks model, and the Next.js caching / revalidation story are all skills the operator wants to develop deeply; Unstash provides production-quality work as the practice ground.
- **Deepest component / library ecosystem in 2026.** shadcn-style component patterns, Radix primitives, TanStack Query, TanStack Form, react-hook-form, and the broader React ecosystem are all available. Less digging for "the React equivalent of X" because X usually is React.
- **Decision made at the cheapest possible moment.** No UI built means no rewrite cost.
- **Auth boundary stays simple.** Next.js is presentation; FastAPI is auth. No NextAuth complexity, no session-state divergence to debug.

### Negative

- **Slower initial frontend velocity than SvelteKit** would have been. The operator has to climb the App Router + RSC mental model, learn the caching / revalidation rules, and internalise the server / client component boundary. This is partly the point (learning value), but it does slow M4 throughput.
- **Polyglot frontend across the operator's portfolio.** Curios and Traiceless are Svelte; Dossier is Remix; Unstash will be Next.js. Context-switching cost between projects is real. Partly the point (broader exposure), but a cost.
- **Discards the Open Props + Bits UI choices from ADR 0001.** Nothing was built on them, so the discard cost is conceptual only — but the design-system choices have to be re-made for the React ecosystem.
- **App Router caching and the server / client boundary have a steeper mental model** than SvelteKit's load / form actions. More footguns: revalidation timing, cookie forwarding from Server Components, dynamic vs static rendering choices, the `'use client'` boundary. This is also a learning benefit but it has a real per-week ergonomic tax.
- **Self-hosting `output: 'standalone'`** is slightly more involved than SvelteKit's `adapter-node`. Both fit the existing Docker + Caddy topology; the Next.js path has a few more moving pieces in the standalone build output. Manageable, not free.

### Neutral

- **Backend, database, search, ML, and deploy infrastructure are unchanged.** This ADR touches only the frontend layer.
- **The visual design language is unchanged.** Open Props tokens carry over; the components rendering against them are different.

## Review criteria

Revisit this decision if:

- **Next.js self-hosting proves operationally heavy relative to value.** If the standalone build, the cache layer, or the RSC boundary repeatedly cause production incidents or eat substantially more operator attention than expected. Revisit candidates would be TanStack Start (if it has matured) or going back to SvelteKit.
- **Unstash's purpose as portfolio capital changes.** If Unstash stops being the operator's flagship portfolio piece — for example, if the product takes off commercially and the portfolio framing is no longer the dominant consideration — the hiring-signal rationale weakens and pure-velocity arguments may favour a different choice.

Informal review at the end of the phase that first builds substantial UI (M4 or M8). Either the decision is reaffirmed because the experience is positive, or surface specific friction points as input to a future ADR.

## References

- ADR 0001 — Initial Stack Choices (Frontend section superseded by this ADR)
- ADR 0002 — CI-driven deployment posture (preserved)
- ADR 0003 — File-based secrets on the VPS (preserved)
- ADR 0006 — Authentication, Session Storage, and Cross-Tenant Admin (cookie model preserved; Next.js acts as BFF in front of it)
- Next.js documentation: [App Router](https://nextjs.org/docs/app), [Server Components](https://nextjs.org/docs/app/getting-started/server-and-client-components), [Server Actions](https://nextjs.org/docs/app/getting-started/updating-data), [`output: 'standalone'`](https://nextjs.org/docs/app/api-reference/config/next-config-js/output#automatically-copying-traced-files)
- Radix UI: [Primitives](https://www.radix-ui.com/primitives)
