# Design system & theming

MegooPM's UI is a Next.js (App Router) + Tailwind v4 + shadcn/ui stack with a
single, token-driven theme. The visual language — sidebar + topbar shell, card
density, indigo brand accent, data-table styling — is adapted from the
[next-shadcn-admin-dashboard](https://github.com/arhamkhnz/next-shadcn-admin-dashboard)
reference. Everything is themeable in **light** and **dark**.

## Ground rules

- **Never hardcode a color** (`#hex`, raw `oklch(...)`, or a numbered Tailwind
  utility like `bg-emerald-500`) in a component. Use a semantic token utility:
  `bg-primary`, `text-muted-foreground`, `border-border`, `bg-success/10`, etc.
- Tokens are defined **once** in [`src/app/globals.css`](../src/app/globals.css)
  and are the only place raw color values live.
- Both themes must clear **WCAG AA** contrast. The token values are pre-tuned
  for AA on their intended surfaces; keep it that way when editing.

## How tokens are wired

Three pieces in `globals.css` work together:

1. **`@theme inline { … }`** maps each design token to a Tailwind utility.
   `--color-primary: var(--primary)` is what makes `bg-primary` / `text-primary`
   exist. Radius utilities (`rounded-lg` …) derive from `--radius`.
2. **`:root { … }`** supplies the **light** values.
3. **`.dark { … }`** overrides them for **dark**. The `.dark` class is toggled on
   `<html>` by `next-themes`.

To change the whole look, edit the values in `:root` / `.dark` — components
follow automatically.

### Token reference

| Token (utility)                          | Purpose                                            |
| ---------------------------------------- | -------------------------------------------------- |
| `background` / `foreground`              | Page canvas + default text                         |
| `card` / `card-foreground`               | Card/surface panels (lifted off the canvas)        |
| `popover` / `popover-foreground`         | Menus, dropdowns, dialogs                          |
| `primary` / `primary-foreground`         | Brand indigo; primary buttons, active accents      |
| `secondary` / `secondary-foreground`     | Low-emphasis buttons/fills                         |
| `muted` / `muted-foreground`             | Subtle fills; secondary/help text                  |
| `accent` / `accent-foreground`           | Hover/selected states (faint indigo tint)          |
| `destructive`                            | Errors, delete actions                             |
| `success` / `success-foreground`         | Healthy/enabled status (green)                     |
| `warning` / `warning-foreground`         | Expiring/at-risk status (amber)                    |
| `border` / `input` / `ring`              | Hairlines, field borders, focus rings              |
| `chart-1` … `chart-5`                    | Categorical data-viz palette                       |
| `sidebar*`                               | Nav rail surface, text, active item, borders       |
| `--radius`                               | Corner radius scale (`rounded-sm`…`rounded-4xl`)   |

Semantic status colors (`success`, `warning`) are tokens too — use
`<Badge variant="success">` / `variant="warning">`, or `text-warning`,
`bg-success/10`, `border-warning/30` for inline states rather than reaching for
`emerald-*` / `amber-*`.

## The theme toggle

- Provider: [`src/components/providers.tsx`](../src/components/providers.tsx)
  wraps the app in `next-themes` (`attribute="class"`, `defaultTheme="system"`,
  `enableSystem`, `disableTransitionOnChange`).
- Control: [`src/components/mode-toggle.tsx`](../src/components/mode-toggle.tsx)
  is a **System / Light / Dark** dropdown in the topbar. The choice persists to
  `localStorage`; `system` follows the OS and updates live.
- **No FOUC**: `next-themes` injects a blocking inline script that sets the
  `.dark` class before first paint, and `<html>` carries `suppressHydrationWarning`
  in [`src/app/layout.tsx`](../src/app/layout.tsx). Don't read `resolvedTheme`
  during render to gate markup — it's `undefined` on the server and causes
  hydration flicker.

## Adding a themed page

1. Put the route under `src/app/(app)/` so it inherits the shell (sidebar +
   topbar) from `(app)/layout.tsx`. Add a nav entry in
   [`src/config/nav.ts`](../src/config/nav.ts).
2. Build with token utilities only:

   ```tsx
   <Card>                              {/* bg-card, border, shadow-xs — themed */}
     <CardHeader>
       <CardTitle>Hosts</CardTitle>
       <CardDescription className="text-muted-foreground">…</CardDescription>
     </CardHeader>
     <CardContent>
       <Button>Add host</Button>       {/* bg-primary */}
       <Badge variant="success">Active</Badge>
     </CardContent>
   </Card>
   ```

3. Reuse shadcn primitives from `src/components/ui/*` (button, card, table,
   dialog, input, badge, …). They already read the tokens, so they render
   correctly in both themes with zero extra work.
4. Verify by toggling System → Light → Dark. If a color looks wrong in one
   theme, the fix belongs in `globals.css`, not the component.
