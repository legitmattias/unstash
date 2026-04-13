# Frontend

SvelteKit frontend for Unstash.

## Stack

- SvelteKit with TypeScript (Svelte 5 runes mode)
- Open Props for design tokens and CSS normalization
- Svelte scoped styles for component CSS
- Bits UI for headless accessible components
- Iconify for icons
- `@sveltejs/adapter-node` for Docker deployment
- pnpm for package management

## Development

```sh
pnpm install
pnpm run dev
```

## Type checking and build

```sh
pnpm run check    # svelte-check
pnpm run build    # production build via adapter-node
```

## Getting started

See the top-level [README](../README.md) for the full development setup using Docker Compose.
