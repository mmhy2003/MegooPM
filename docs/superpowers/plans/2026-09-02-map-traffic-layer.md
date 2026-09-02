# Map Traffic Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show recorded visitor traffic on the dashboard map beside the existing threat layer, switched by a toggle.

**Architecture:** Both endpoints return `{country, count}`; the map component maps a country to a centroid and draws it. One placement rule, so a country is always in the same place — which is also why the layers cannot draw together and a toggle switches between them.

**Tech Stack:** Python 3.12, FastAPI, pytest; Next.js 16, React 19, cobe, vitest.

**Spec:** `docs/superpowers/specs/2026-09-02-map-traffic-layer-design.md`

## Global Constraints

- **The canvas must not be remounted when the layer changes.** Recreating it tears down and rebuilds the WebGL context on every toggle, which flickers and leaks contexts. This is why the toggle is two `aria-pressed` buttons rather than `Tabs` — matched tab panels would duplicate the canvas element.
- **A country with no centroid is listed, never dropped.** The count is real even when the position is unknown; hiding it would understate the data to keep the map tidy. This path already exists in the component.
- **The two empty states must say different things.** "No attacks flagged" and "No visitors recorded" are not the same statement, and the threat one must keep saying that nothing flagged is not nothing happening.
- **`AlertSource.latitude`/`longitude` stay.** Only `threats.py` stops using them; `GET /crowdsec/alerts` still exposes them and they document what LAPI sends.
- Run backend tests in a Linux container — the app imports `fcntl`:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6" "maxminddb>=2.6"
```

  Do NOT mount the working tree over `/app`. Tear down with `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.
- Removing response fields breaks frontend fixtures that supply them. `vitest` will not catch it; `npm run typecheck` will.

---

### Task 1: Drop coordinates from the threat endpoint

**Files:**
- Modify: `backend/app/schemas/dashboard.py`
- Modify: `backend/app/services/dashboard/threats.py`
- Modify: `backend/tests/test_dashboard_threats.py`
- Modify: `backend/openapi.json` (regenerated)

**Interfaces:**
- Produces: `ThreatPoint(country: str, count: int)` — no `lat`/`lng`.

- [x] **Step 1: Update the failing tests first**

In `backend/tests/test_dashboard_threats.py`, delete the two coordinate tests
(`test_coordinates_come_from_the_alerts_themselves` and
`test_a_country_with_mixed_coordinates_averages_only_the_known_ones`), drop the
`lat`/`lng` arguments from `_alert`, and replace the "still counted" test with
one that no longer mentions position:

```python
def _alert(cn: str | None) -> Alert:
    return Alert(scenario="x", source=AlertSource(ip="1.2.3.4", cn=cn))


def test_a_country_is_counted_regardless_of_where_it_is() -> None:
    """Placement moved to the map; the service only counts. A country the map
    cannot place is still a real country with real attacks."""
    points = group_by_country([_alert("DE"), _alert("DE")])
    assert points[0].country == "DE"
    assert points[0].count == 2
    assert not hasattr(points[0], "lat")
```

- [x] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_dashboard_threats.py -p no:cacheprovider
```

Expected: FAIL — `ThreatPoint` still requires `lat`/`lng`, so `hasattr` is true.

- [x] **Step 3: Simplify the schema**

In `backend/app/schemas/dashboard.py`:

```python
class ThreatPoint(BaseModel):
    """One country's attack count.

    Position is deliberately absent: the map owns placement, so both this and
    the visitor countries arrive in the same shape and a country is always
    drawn in the same spot. Sending coordinates that never change on every poll
    bought nothing.
    """

    country: str
    count: int
```

- [x] **Step 4: Simplify the service**

In `backend/app/services/dashboard/threats.py`, delete `_mean`, the `lats` and
`lngs` accumulators, and the coordinate arguments to `ThreatPoint`. The loop
keeps only the counting:

```python
def group_by_country(alerts: Iterable[Alert]) -> list[ThreatPoint]:
    """Count alerts per country.

    An alert with no country is dropped: an "unknown" bucket cannot be drawn
    and would distort the ranking of the countries that can.
    """
    counts: dict[str, int] = {}
    for alert in alerts:
        source = alert.source
        if source is None or not source.cn:
            continue
        counts[source.cn.upper()] = counts.get(source.cn.upper(), 0) + 1

    # Count descending, then country ascending: a stable order, so two
    # identical polls do not reshuffle the map's legend.
    ordered = sorted(counts, key=lambda code: (-counts[code], code))
    return [ThreatPoint(country=code, count=counts[code]) for code in ordered]
```

Update the module docstring: it currently explains that coordinates come from
CrowdSec, which stops being true.

- [x] **Step 5: Run the tests and refresh the contract**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass, ruff clean.

- [x] **Step 6: Commit**

```bash
git add backend
git commit -m "refactor(dashboard): let the map place countries, not the API"
```

---

### Task 2: The centroid table

**Files:**
- Create: `frontend/src/components/dashboard/country-centroids.ts`
- Test: `frontend/src/components/dashboard/country-centroids.test.ts` (create)

**Interfaces:**
- Produces: `centroidFor(code: string): [number, number] | null`.

- [x] **Step 1: Write the failing tests**

Create `frontend/src/components/dashboard/country-centroids.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { centroidFor } from "@/components/dashboard/country-centroids";

describe("centroidFor", () => {
  it("places a known country", () => {
    const point = centroidFor("DE");
    expect(point).not.toBeNull();
    // Germany: roughly 51N, 10E. Loose bounds — this guards against a
    // transposed lat/lng pair, which would put it in the Indian Ocean.
    expect(point![0]).toBeGreaterThan(45);
    expect(point![0]).toBeLessThan(56);
    expect(point![1]).toBeGreaterThan(5);
    expect(point![1]).toBeLessThan(16);
  });

  it("normalises case", () => {
    // Country codes reach this from two sources and only one upper-cases them.
    expect(centroidFor("de")).toEqual(centroidFor("DE"));
  });

  it("returns null for a country it does not know", () => {
    expect(centroidFor("ZZ")).toBeNull();
  });

  it("returns null for junk rather than throwing", () => {
    expect(centroidFor("")).toBeNull();
    expect(centroidFor("NOT-A-CODE")).toBeNull();
  });

  it("keeps every entry inside real coordinate bounds", () => {
    // A transposed or mistyped pair is the likeliest error in a hand-written
    // table, and it is invisible on a globe until someone notices Brazil in
    // the Pacific.
    for (const [code, [lat, lng]] of Object.entries(COUNTRY_CENTROIDS)) {
      expect(Math.abs(lat), code).toBeLessThanOrEqual(90);
      expect(Math.abs(lng), code).toBeLessThanOrEqual(180);
    }
  });

  it("uses two-letter uppercase keys throughout", () => {
    for (const code of Object.keys(COUNTRY_CENTROIDS)) {
      expect(code).toMatch(/^[A-Z]{2}$/);
    }
  });
});
```

Import `COUNTRY_CENTROIDS` alongside `centroidFor`.

- [x] **Step 2: Run the tests to verify they fail**

```bash
cd frontend && npx vitest run src/components/dashboard/country-centroids.test.ts
```

Expected: FAIL — the module does not exist.

- [x] **Step 3: Write the table**

Create `frontend/src/components/dashboard/country-centroids.ts`. Static view
data: approximate country centroids, `[lat, lng]`.

Deliberately not exhaustive — a country absent here is listed with its count
but not plotted, which is the same path the component already takes for a
country it cannot place. Add entries as they come up; there is no logic to
break.

```ts
/**
 * Approximate country centroids for the dashboard map.
 *
 * Static view data, kept in the frontend because placement is a view concern:
 * both the threat and traffic layers send only {country, count}, and this is
 * what turns that into a position. One table means a country is always drawn
 * in the same place regardless of which layer drew it.
 *
 * Not exhaustive on purpose. A country missing here is still listed with its
 * count, just not plotted — the honest degradation, and the same one used for
 * data that arrives unlocatable.
 */
export const COUNTRY_CENTROIDS: Record<string, [number, number]> = {
  AE: [23.42, 53.85],
  AF: [33.94, 67.71],
  AL: [41.15, 20.17],
  AM: [40.07, 45.04],
  AO: [-11.2, 17.87],
  AR: [-38.42, -63.62],
  AT: [47.52, 14.55],
  AU: [-25.27, 133.78],
  AZ: [40.14, 47.58],
  BA: [43.92, 17.68],
  BD: [23.68, 90.36],
  BE: [50.5, 4.47],
  BG: [42.73, 25.49],
  BH: [26.07, 50.56],
  BO: [-16.29, -63.59],
  BR: [-14.24, -51.93],
  BY: [53.71, 27.95],
  CA: [56.13, -106.35],
  CD: [-4.04, 21.76],
  CH: [46.82, 8.23],
  CI: [7.54, -5.55],
  CL: [-35.68, -71.54],
  CM: [7.37, 12.35],
  CN: [35.86, 104.2],
  CO: [4.57, -74.3],
  CR: [9.75, -83.75],
  CU: [21.52, -77.78],
  CY: [35.13, 33.43],
  CZ: [49.82, 15.47],
  DE: [51.17, 10.45],
  DK: [56.26, 9.5],
  DO: [18.74, -70.16],
  DZ: [28.03, 1.66],
  EC: [-1.83, -78.18],
  EE: [58.6, 25.01],
  EG: [26.82, 30.8],
  ES: [40.46, -3.75],
  ET: [9.15, 40.49],
  FI: [61.92, 25.75],
  FR: [46.23, 2.21],
  GB: [55.38, -3.44],
  GE: [42.32, 43.36],
  GH: [7.95, -1.02],
  GR: [39.07, 21.82],
  GT: [15.78, -90.23],
  HK: [22.32, 114.17],
  HN: [15.2, -86.24],
  HR: [45.1, 15.2],
  HU: [47.16, 19.5],
  ID: [-0.79, 113.92],
  IE: [53.41, -8.24],
  IL: [31.05, 34.85],
  IN: [20.59, 78.96],
  IQ: [33.22, 43.68],
  IR: [32.43, 53.69],
  IS: [64.96, -19.02],
  IT: [41.87, 12.57],
  JM: [18.11, -77.3],
  JO: [30.59, 36.24],
  JP: [36.2, 138.25],
  KE: [-0.02, 37.91],
  KG: [41.2, 74.77],
  KH: [12.57, 104.99],
  KR: [35.91, 127.77],
  KW: [29.31, 47.48],
  KZ: [48.02, 66.92],
  LA: [19.86, 102.5],
  LB: [33.85, 35.86],
  LK: [7.87, 80.77],
  LT: [55.17, 23.88],
  LU: [49.82, 6.13],
  LV: [56.88, 24.6],
  LY: [26.34, 17.23],
  MA: [31.79, -7.09],
  MD: [47.41, 28.37],
  ME: [42.71, 19.37],
  MG: [-18.77, 46.87],
  MK: [41.61, 21.75],
  MM: [21.91, 95.96],
  MN: [46.86, 103.85],
  MT: [35.94, 14.38],
  MU: [-20.35, 57.55],
  MX: [23.63, -102.55],
  MY: [4.21, 101.98],
  MZ: [-18.67, 35.53],
  NG: [9.08, 8.68],
  NI: [12.87, -85.21],
  NL: [52.13, 5.29],
  NO: [60.47, 8.47],
  NP: [28.39, 84.12],
  NZ: [-40.9, 174.89],
  OM: [21.51, 55.92],
  PA: [8.54, -80.78],
  PE: [-9.19, -75.02],
  PH: [12.88, 121.77],
  PK: [30.38, 69.35],
  PL: [51.92, 19.15],
  PR: [18.22, -66.59],
  PT: [39.4, -8.22],
  PY: [-23.44, -58.44],
  QA: [25.35, 51.18],
  RO: [45.94, 24.97],
  RS: [44.02, 21.01],
  RU: [61.52, 105.32],
  RW: [-1.94, 29.87],
  SA: [23.89, 45.08],
  SD: [12.86, 30.22],
  SE: [60.13, 18.64],
  SG: [1.35, 103.82],
  SI: [46.15, 14.99],
  SK: [48.67, 19.7],
  SN: [14.5, -14.45],
  SV: [13.79, -88.9],
  SY: [34.8, 38.997],
  TH: [15.87, 100.99],
  TN: [33.89, 9.54],
  TR: [38.96, 35.24],
  TT: [10.69, -61.22],
  TW: [23.7, 120.96],
  TZ: [-6.37, 34.89],
  UA: [48.38, 31.17],
  UG: [1.37, 32.29],
  US: [37.09, -95.71],
  UY: [-32.52, -55.77],
  UZ: [41.38, 64.59],
  VE: [6.42, -66.59],
  VN: [14.06, 108.28],
  YE: [15.55, 48.52],
  ZA: [-30.56, 22.94],
  ZM: [-13.13, 27.85],
  ZW: [-19.02, 29.15],
};

/** The centroid for an ISO-3166 alpha-2 code, or null if unknown. */
export function centroidFor(code: string): [number, number] | null {
  if (!code) return null;
  return COUNTRY_CENTROIDS[code.toUpperCase()] ?? null;
}
```

- [x] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/components/dashboard/country-centroids.test.ts
```

Expected: PASS, 6 tests. The bounds test is the valuable one — it catches a
transposed `[lng, lat]` pair, which is invisible on a globe until someone
notices Brazil in the Pacific.

- [x] **Step 5: Commit**

```bash
git add frontend/src/components/dashboard/country-centroids.ts frontend/src/components/dashboard/country-centroids.test.ts
git commit -m "feat(dashboard): country centroids for the map"
```

---

### Task 3: The two-layer globe

**Files:**
- Rename: `frontend/src/components/dashboard/threat-globe.tsx` → `origin-globe.tsx`
- Rename: its test → `origin-globe.test.tsx`
- Modify: `frontend/src/components/dashboard/dashboard-view.tsx`

**Interfaces:**
- Consumes: `centroidFor` (Task 2), `ThreatPoint` and `CountryCount` from the API client.
- Produces: `<OriginGlobe threats={ThreatPoint[]} traffic={CountryCount[]} />`.

- [x] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
```

`ThreatPoint` loses `lat`/`lng`, which will break the existing globe test
fixtures — that is expected and Step 2 replaces them.

- [x] **Step 2: Write the failing tests**

Rename the test file to `origin-globe.test.tsx` and replace its contents:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OriginGlobe } from "@/components/dashboard/origin-globe";

// jsdom has no WebGL, so the globe cannot draw. That is the point: the
// component must still present its data, which is what a screen reader gets.
vi.mock("cobe", () => ({
  default: () => {
    throw new Error("no webgl in jsdom");
  },
}));

const THREATS = [{ country: "DE", count: 9 }];
const TRAFFIC = [{ country: "FR", visitors: 3, requests: 40 }];

afterEach(() => cleanup());

describe("OriginGlobe", () => {
  it("shows traffic first, because it describes the whole site", async () => {
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);
    expect(await screen.findByText("FR")).toBeInTheDocument();
    expect(screen.queryByText("DE")).not.toBeInTheDocument();
  });

  it("switches the list when the layer changes", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText("DE")).toBeInTheDocument();
    expect(screen.queryByText("FR")).not.toBeInTheDocument();
  });

  it("marks the active layer for assistive technology", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={THREATS} traffic={TRAFFIC} />);

    expect(screen.getByRole("button", { name: /traffic/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: /threats/i }));
    expect(screen.getByRole("button", { name: /threats/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("says something different for each empty layer", async () => {
    const user = userEvent.setup();
    render(<OriginGlobe threats={[]} traffic={[]} />);

    expect(screen.getByText(/no visitors recorded/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText(/no attacks flagged/i)).toBeInTheDocument();
    // Still says what an empty threat list does NOT mean.
    expect(screen.getByText(/not that nothing happened/i)).toBeInTheDocument();
  });

  it("lists a country it cannot place, with its count", async () => {
    // Dropping it would understate the data to keep the map tidy.
    render(<OriginGlobe threats={[{ country: "ZZ", count: 4 }]} traffic={[]} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /threats/i }));

    expect(screen.getByText("ZZ")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/not located/i)).toBeInTheDocument();
  });

  it("orders each layer busiest first", async () => {
    render(
      <OriginGlobe
        threats={[]}
        traffic={[
          { country: "DE", visitors: 1, requests: 5 },
          { country: "FR", visitors: 9, requests: 90 },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    // The API already orders by requests; the component must not reshuffle.
    expect(items[0]).toContain("DE");
  });
});
```

- [x] **Step 3: Run the tests to verify they fail**

```bash
cd frontend && npx vitest run src/components/dashboard/origin-globe.test.tsx
```

Expected: FAIL — the module does not exist.

- [x] **Step 4: Build the component**

Rename `threat-globe.tsx` to `origin-globe.tsx` and rework it. Keep everything
that already works — the `keepMounted`-style canvas, the WebGL fallback, the
`useMemo` on the plottable list, the ranked list — and change three things:

1. It takes `threats` and `traffic` and normalises both to
   `{country, count}[]` internally, so everything downstream is layer-agnostic.
2. A `useState<"traffic" | "threats">` defaulting to `"traffic"`, with two
   buttons carrying `aria-pressed`.
3. Positions come from `centroidFor(country)` rather than from the data.

**The canvas element must stay mounted across the switch.** Render it once,
outside any conditional keyed on the layer; only the marker list passed to
`cobe` changes. Remounting it rebuilds the WebGL context on every toggle, which
flickers and leaks contexts.

The markers become:

```tsx
const active = layer === "traffic" ? trafficPoints : threatPoints;
const plottable = useMemo(
  () =>
    active
      .map((p) => ({ ...p, position: centroidFor(p.country) }))
      .filter((p) => p.position !== null),
  [active],
);
```

and the list renders `active`, showing `not located` when
`centroidFor(country)` is null.

The toggle is new code, so here it is in full. Two buttons rather than `Tabs`,
because matched tab panels would duplicate the canvas element and rebuild the
WebGL context on every switch:

```tsx
<div className="flex gap-1" role="group" aria-label="Map layer">
  {(["traffic", "threats"] as const).map((option) => (
    <Button
      key={option}
      size="sm"
      variant={layer === option ? "default" : "ghost"}
      aria-pressed={layer === option}
      onClick={() => setLayer(option)}
    >
      {option === "traffic" ? "Traffic" : "Threats"}
    </Button>
  ))}
</div>
```

and the empty state picks its message from the active layer, because the two
statements are not interchangeable:

```tsx
{active.length === 0 ? (
  layer === "traffic" ? (
    <p className="text-muted-foreground text-sm">
      No visitors recorded yet — counting starts after the next flush.
    </p>
  ) : (
    <p className="text-muted-foreground text-sm">
      No attacks flagged. This means nothing was detected, not that nothing
      happened.
    </p>
  )
) : null}
```

- [x] **Step 5: Pass both datasets from the view**

In `dashboard-view.tsx`, replace `<ThreatGlobe points={threats} />` with:

```tsx
<OriginGlobe threats={threats} traffic={visitors?.countries ?? []} />
```

and update the import. `visitors` may be null before the first load, hence the
fallback.

- [x] **Step 6: Run the full frontend gate**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

Expected: all pass. `typecheck` is what catches fixtures still supplying the
removed `lat`/`lng`.

- [x] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(dashboard): switch the map between traffic and threats"
```

---

## Manual verification

Not reachable by any automated test:

1. Load the dashboard. The map should default to **Traffic** and show visitor
   countries.
2. Toggle to **Threats**. The globe, the legend and the list must all change
   together — there should be no moment where the list describes one dataset
   and the globe another.
3. Confirm a country appears in the **same place** on both layers. That is the
   whole reason placement moved to the map.
4. Toggle back and forth several times and watch for flicker. The canvas must
   not be rebuilt; if it blinks, it is being remounted.
5. With no visitor data yet, confirm the traffic layer says "no visitors
   recorded" rather than showing an empty globe with no explanation.


---

## Executed 2026-09-02

All three tasks complete. Backend **792 passed, 41 skipped**, ruff clean.
Frontend **435 passed, 1 skipped**, typecheck, lint and build clean. The globe
gained a layer for **+24 KB** of built assets (2523 → 2547 KB), all of it the
centroid table and the toggle.

No deviations from the plan. Two things worth recording:

- **The centroid table shipped with ~150 entries**, not the 249 that exist. A
  country outside it is listed with its count and not plotted, exactly as
  designed, so extending it later needs no code change.
- **The canvas stays mounted across a layer switch**, as the plan required —
  only the marker list and the colour change. Whether it visibly flickers is a
  manual check, since jsdom has no WebGL to test against.
