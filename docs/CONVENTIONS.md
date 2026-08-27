# MegooPM — Repo Conventions

Owned by the Architect. Update this doc when a convention changes; flag stack
changes to the CEO.

## Stack

| Area      | Choice                                              |
| --------- | --------------------------------------------------- |
| Frontend  | Next.js 16 (App Router), React 19, TypeScript strict |
| Styling   | Tailwind CSS v4, shadcn/ui (`base-nova` / Base UI)  |
| Icons     | `lucide-react`                                      |
| Theming   | `next-themes` (light / dark / system)               |
| Testing   | Vitest + Testing Library (jsdom)                    |
| Backend   | FastAPI + Alembic + Postgres                        |
| Jobs      | Celery worker + beat, Redis broker/result backend   |
| Proxy     | nginx (managed by the backend via shared volumes)   |
| Security  | CrowdSec (placeholder; wired up by a later ticket)  |
| Local dev | docker compose (full stack, one command)            |

### Stack note — Next.js 16, not 15

MEG-13 specified "Next.js 15". `create-next-app@latest` now ships **Next 16**
(current stable at the time of scaffolding), which is what this repo uses. The
App Router + TS-strict foundation the PRD asked for is unchanged; the delta is
Next-major internals. Two conventions follow from Next 16 and are called out
below: the `proxy` file (formerly `middleware`) and generated route types.

## Frontend directory conventions

```
frontend/src/
├── app/                      App Router routes
│   ├── (app)/                Authenticated shell (sidebar + top bar)
│   │   ├── layout.tsx        Mounts AppSidebar + AppTopbar
│   │   ├── page.tsx          Redirects to HOME_ROUTE
│   │   └── <area>/page.tsx   One folder per product area
│   ├── login/                Auth skeleton (public route)
│   └── layout.tsx            Root: fonts, <Providers>, theme
├── components/
│   ├── ui/                   shadcn primitives — do NOT hand-edit; re-add via CLI
│   ├── app-sidebar.tsx       Primary navigation
│   ├── app-topbar.tsx        Top bar (trigger, title, theme, profile avatar)
│   └── providers.tsx         Client provider stack (theme, tooltip, toaster)
├── config/nav.ts             Sidebar nav (+ `adminOnly` items, `navForRole`, `utilityRoutes`)
├── lib/
│   ├── api/                  Typed backend client (see below)
│   ├── auth/session.ts       Auth seam (cookie name, enable flag)
│   └── env.ts                Typed env access
├── hooks/                    Reusable hooks
└── proxy.ts                  Auth-aware routing (Next 16 `proxy`, ex-middleware)
```

### Component library

- shadcn is initialized in the **`base-nova`** style, which is built on **Base UI**.
  Composition uses the **`render` prop**, not Radix's `asChild`:

  ```tsx
  // ✅ base-nova
  <SidebarMenuButton render={<Link href="/proxy-hosts" />}>…</SidebarMenuButton>
  // ❌ Radix pattern — not supported here
  <SidebarMenuButton asChild><Link …/></SidebarMenuButton>
  ```

- Add primitives with `npx shadcn@latest add <component>`. Treat files under
  `components/ui/` as generated: prefer re-running the CLI over manual edits.

### Design tokens & theming

- Light/dark tokens live as CSS variables in `src/app/globals.css` (`:root` and
  `.dark`). Reference them through Tailwind utilities (`bg-background`,
  `text-muted-foreground`, `bg-sidebar`, …) — never hard-code colors.
- `next-themes` drives the `.dark` class; `ModeToggle` flips it.

### API client (`lib/api`)

- `apiFetch<T>(path, opts)` / the `api.{get,post,…}` helpers own URL building,
  JSON (de)serialization, auth headers, and error normalization.
- Base URL comes from `NEXT_PUBLIC_API_BASE_URL` via `lib/env.ts`.
- Non-2xx responses throw `ApiError` (carries `status` + parsed `detail`).
- Feature tickets add typed resource modules under `lib/api/resources/` and
  re-export them from `lib/api/index.ts`. Do not call `fetch` directly in
  feature code.
- Auth token injection goes through `setAuthTokenProvider(...)`.

### Auth skeleton

- `proxy.ts` redirects unauthenticated requests to `/login` **only when**
  `NEXT_PUBLIC_AUTH_ENABLED=true`. Off by default so the shell is browsable in
  dev. Session cookie name: `megoopm_session` (`lib/auth/session.ts`).

### Route types (Next 16)

- Next generates `LayoutProps<...>` / `PageProps<...>` globals into `.next/types`.
  `npm run typecheck` runs `next typegen` first so it works on a clean checkout.

## Quality gates

Every frontend change must pass, and CI enforces:

```bash
npm run lint        # eslint (flat config, next core-web-vitals + ts)
npm run typecheck   # next typegen && tsc --noEmit
npm run test        # vitest run
```

- Tests: colocate as `*.test.ts(x)` next to the unit under test.
- Vitest transpiles JSX via esbuild's automatic runtime (reads `jsx` from
  tsconfig). We deliberately avoid `@vitejs/plugin-react` because its babel
  dependency conflicts with the `shadcn` CLI's babel pin.

## Local dev orchestration

The whole system runs locally from the repo root with one command:

```bash
cp .env.example .env    # optional — compose has sane defaults baked in
docker compose up --build
```

- **`docker-compose.yml` (root)** is the golden-path full stack: `db` (Postgres),
  `redis`, `backend` (uvicorn), `worker` + `beat` (Celery), `frontend`
  (`next dev`), `nginx` (the managed proxy), and a `crowdsec` placeholder.
  `backend/docker-compose.yml` remains a lighter db+api-only stack for
  backend-focused work.
- **One `.env` at the root** configures every service. Compose interpolates it
  with `${VAR:-default}`, so the stack also boots with no `.env` at all; copy
  the file only to change ports/credentials. Per-package `.env` files
  (`backend/.env`, `frontend/.env.local`) are for running a package standalone.
- **Images:** backend and frontend each own a `Dockerfile`. Worker and beat
  reuse the backend image with a different `command` and `RUN_MIGRATIONS=0` —
  the `backend` service is the single migration owner.
- **Managed proxy contract:** the backend writes vhosts to `/etc/nginx/conf.d`
  and TLS certs to `/etc/nginx/certs`, both **shared named volumes**
  (`nginx_confd`, `nginx_certs`). `infra/nginx/nginx.conf` is the read-only base
  config that `include`s conf.d. A one-shot `proxy-init` service chowns those
  volumes to the backend user (uid 1000) so the backend can write while nginx
  reads. Backend paths come from the `nginx_confd_dir` / `nginx_certs_dir`
  settings.
- **Browser vs. in-network URLs:** `NEXT_PUBLIC_API_BASE_URL` must be the
  backend's *published host* URL (`http://localhost:8000`), because the SPA
  calls it from the user's browser — not the in-network `backend` hostname.
- **Health & ordering:** every long-running service has a healthcheck;
  `depends_on` uses `service_healthy` / `service_completed_successfully` so
  `docker compose up` converges to a healthy stack. `crowdsec` has no dependents
  and never blocks startup.
- `make help` lists shortcuts (`up`, `down`, `clean`, `logs`, `migrate`, …).

## Ticket sizing

Each ticket should be completable in one agent session with an unambiguous
definition of done. Feature tickets build on this foundation: a new product
area = a route folder under `app/(app)/`, a typed API resource module, and its
tests — nothing here needs to be re-scaffolded.

Admin-only areas set `adminOnly: true` on their `NavItem`; the sidebar renders
`navForRole(user.role)` so members never see them (the API's 403 is the real
gate). Pages reached from the topbar avatar rather than the sidebar (e.g.
`/profile`) are titled via `utilityRoutes` instead of a nav entry.
