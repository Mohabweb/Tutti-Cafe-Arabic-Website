# Tutti Cafe

An Arabic-only, right-to-left static redesign of the Tutti Cafe website.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm --filter @workspace/tutti-cafe run dev` — run the static Tutti Cafe site
- `pnpm --filter @workspace/tutti-cafe run build` — build all static pages and copy JSON data
- `pnpm --filter @workspace/tutti-cafe run scrape` — refresh source data and local images
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

The Tutti Cafe artifact itself has no backend or database. The API and database packages belong to the separate workspace starter and are not used by this static site.

## Stack

- Tutti Cafe: semantic HTML, CSS, vanilla JavaScript, JSON, and Vite for static development/build
- Scraper: Python, Requests, BeautifulSoup, with optional ImageMagick WebP optimization
- Workspace: pnpm monorepo; unrelated API starter uses Express, PostgreSQL, and Drizzle

## Where things live

- `artifacts/tutti-cafe/index.html` and the other HTML pages — public site pages
- `artifacts/tutti-cafe/css/` and `artifacts/tutti-cafe/js/` — shared styles and browser behavior
- `artifacts/tutti-cafe/data/` — the three source-of-truth JSON data files
- `artifacts/tutti-cafe/public/assets/` — local logo and scraped images
- `artifacts/tutti-cafe/scripts/scrape.py` — public-page scraper and data normalizer
- `artifacts/tutti-cafe/issues.md` — verified source anomalies and omissions

## Architecture decisions

- Keep the customer-facing site static and data-driven; do not connect it to the separate API/database packages.
- Arabic copy and verified scraped/owner-provided facts are the only source for visible business content.
- Missing source values remain missing. Never fabricate prices, descriptions, reviews, or current branch counts.
- Preserve the seven currently listed branches; the older about-page claim of more than thirteen branches is omitted from current copy and documented as a source conflict.

## Product

The site presents Tutti Cafe's story, 19 menu categories, searchable menu with size choices, seven branch locations, photo gallery, current jobs information, and accessible PDF menu links. There is no online ordering or account flow.

## User preferences

- Keep this site Arabic-only and RTL.
- Use plain HTML, CSS, and vanilla JavaScript; no React, backend, or database for the Tutti Cafe artifact.
- Do not invent menu items, prices, or numbers; log source gaps and anomalies in `issues.md`.

## Gotchas

- Menu names with size suffixes must be consolidated, including the verified spelling/order aliases recorded by the scraper.
- `price: null` represents an unavailable or source-placeholder price; do not display it as zero.
- The brand's current branch count comes from the seven listed branch records, not the conflicting about-page claim.

## Pointers

- See the `pnpm-workspace` skill for monorepo structure and package details.
