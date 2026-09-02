# The map's traffic layer — design

## Goal

Show recorded visitor traffic on the dashboard map, alongside the existing
threat layer, switched by a toggle.

## Scope

This is **P3** of the dashboard decomposition:

| | | |
| --- | --- | --- |
| P1 | Dashboard on data that already exists | shipped |
| P2 | Visitor analytics: ingestion, storage, retention | shipped |
| **P3** | Traffic layer on the map | this spec |
| P4 | Realtime push, replacing polling | not started |

P3 is much smaller than originally scoped. It was to include the GeoIP work,
but P2 absorbed that: visitor rows already carry a country. What remains is
rendering.

## The problem this solves

The two data sources disagree about position:

- **Threats** carry `lat`/`lng`, resolved by CrowdSec per alert and averaged
  per country by `group_by_country`.
- **Visitors** carry a country code and nothing else. `visitor_day` has no
  coordinates, because the bundled database is country-only.

So a traffic layer needs positions from somewhere, and if each source supplies
its own, the same country renders at two slightly different points depending on
which layer drew it — which looks like a bug and is impossible to explain.

## The decision: the map owns placement

**Both endpoints return `{country, count}`. The map component decides where
that goes**, from a country→centroid table shipped in the frontend.

Consequences, all of them wanted:

- One placement rule, so a country is always in the same place.
- The backend stops serialising coordinates that never change, on every poll.
- Placement becomes a view concern, which is what it is.

`ThreatPoint` therefore loses `lat` and `lng`. This is a schema change to an
endpoint shipped the same day; nothing outside this repo consumes it.

**What is given up:** CrowdSec's real per-alert coordinates, currently averaged
into a per-country point. That average is genuinely more informative than a
centroid — it says roughly *where in* a country the attackers were. But the map
already groups by country, so the loss is a nuance the current visualisation
cannot express, and keeping it costs the inconsistency above.

## The layers do not draw together

With unified placement, a country's traffic marker and its threat marker land
on the **identical** point. Drawn together they stack exactly and one silently
hides the other, so the map would under-report whichever layer draws first.

So: **a toggle, one layer at a time.** The control sits above the globe, and
switching it changes the markers, the legend and the ranked list together —
there is never a moment where the list describes one dataset and the globe
another.

Each layer's empty state says something different, because the two statements
are not the same:

- Threats: *"No attacks flagged"* — and explicitly, that this means nothing was
  detected, not that nothing happened.
- Traffic: *"No visitors recorded"* — the counting may simply not have flushed
  yet.

## The centroid table

`country-centroids.ts` in the frontend: a map of ISO-3166 alpha-2 codes to
approximate country centroids. Static view data, a few kilobytes, living where
it is used.

**It will not be exhaustive.** It is written by hand, so it covers the
countries that can be sourced confidently rather than all 249. A country
missing from it is **listed but not plotted**, which is exactly what the
component already does for a country with no position — so the gap degrades
through an existing path rather than needing new handling, and the table can be
extended later without touching any design.

A wrong centroid puts a dot in the wrong place, which is visible and
correctable; a missing one hides a dot while keeping the number, which is
honest. Neither loses data.

## Components

`ThreatGlobe` becomes layer-agnostic and is renamed `OriginGlobe`. It takes
both datasets and owns the toggle, so the globe, legend and list cannot
disagree about which layer is showing. `DashboardView` passes the two lists and
nothing else.

```
DashboardView
  └── OriginGlobe
        ├── toggle: Traffic | Threats
        ├── canvas   (markers for the active layer)
        └── list     (the same layer, ranked)
```

## The overlap with the visitors card

The visitors card already lists countries by request volume, and the traffic
layer will rank the same countries again. That redundancy is accepted rather
than removed: the card answers "how much traffic, from where" as numbers, and
the map answers "where in the world" as a picture, and readers reach for
different ones.

What is **not** acceptable is the two disagreeing. Both read the same
`countries` array from the same response, so they cannot — and neither
recomputes anything the other computes.

If the duplication grates once it is on screen, the cheaper fix is to trim the
card's country list rather than the map's, since the card also carries totals
and busiest addresses that have nowhere else to go.

## Data flow

```
GET /dashboard/threats   ──> [{country, count}] ─┐
                                                 ├─> OriginGlobe ──> centroids ──> markers
GET /dashboard/visitors  ──> countries[]  ───────┘
```

The visitors endpoint is unchanged: its `countries` array already carries
`{country, visitors, requests}`, and the map uses `requests` as the count.

## Testing

**The centroid lookup:** a known code, an unknown code, and lower-case input —
country codes arrive from two different sources and only one of them normalises.

**The toggle:** switching changes both the markers and the list, so they can
never describe different datasets.

**The empty states:** each layer's message is distinct, and the threat one still
says that "nothing flagged" is not "nothing happened".

**The existing rule survives:** a country with no centroid is still listed with
its count.

**Not automatable:** whether the dots land where a reader expects on a rotating
globe.

## Files

**Backend**

- `app/schemas/dashboard.py` — `ThreatPoint` loses `lat`/`lng`
- `app/services/dashboard/threats.py` — stop averaging coordinates
- `tests/test_dashboard_threats.py` — the coordinate assertions go
- `openapi.json` — regenerated

**Frontend**

- `src/components/dashboard/country-centroids.ts` (new)
- `src/components/dashboard/origin-globe.tsx` (renamed from `threat-globe.tsx`)
- `src/components/dashboard/dashboard-view.tsx` — pass both datasets
- regenerated API types

## Non-goals

- **Per-city detail.** The bundled database is country-only and the map groups
  by country.
- **Which host a visitor reached.** That grain does not exist in `visitor_day`,
  deliberately, per the P2 spec.
- **A combined view.** Rejected above: identical centroids make stacked markers
  hide each other.

## Open risks

**The centroid table is hand-written.** A wrong entry misplaces a country until
someone notices. The mitigation is that it is inert data with no logic, and a
correction is a one-line change with no migration.

**This churns an endpoint shipped hours earlier.** The schema, the OpenAPI
contract, the generated types and the existing globe tests all move for what is
visually a small feature. It is worth it only because the alternative bakes in
two placement rules that will disagree forever.
