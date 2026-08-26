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
| Backend   | FastAPI + Alembic + Postgres (added by later tickets) |

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
│   ├── app-topbar.tsx        Top bar (trigger, title, theme, account)
│   └── providers.tsx         Client provider stack (theme, tooltip, toaster)
├── config/nav.ts             Single source of truth for sidebar nav
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

## Ticket sizing

Each ticket should be completable in one agent session with an unambiguous
definition of done. Feature tickets build on this foundation: a new product
area = a route folder under `app/(app)/`, a typed API resource module, and its
tests — nothing here needs to be re-scaffolded.
